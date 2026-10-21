"""Stripe, using the merchant's own Stripe account: cards (+Apple/Google Pay), and bank
debits (US ACH, SEPA, UK Bacs, AU BECS) for customers outside India.

Uses hosted Checkout Sessions, so card and bank numbers are typed into Stripe's page and never
touch Sangam's servers (keeps you in the lightest PCI DSS scope, SAQ A)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from .base import (CreateResult, MandateResult, Provider, ProviderContext, ProviderError, RefundResult,
                   SyncResult, WebhookResult, WebhookSignatureError)

API = "https://api.stripe.com/v1"
BANK_DEBIT_BY_CURRENCY = {"USD": "us_bank_account", "EUR": "sepa_debit", "GBP": "bacs_debit", "AUD": "au_becs_debit"}


class StripeProvider(Provider):
    name = "stripe"
    display_name = "Stripe"
    methods = {"card", "bank_debit"}
    credential_fields = ["secret_key"]  # webhook_secret is optional but recommended

    def supports(self, method: str, currency: str) -> bool:
        if method == "bank_debit":
            return currency in BANK_DEBIT_BY_CURRENCY
        return method == "card"

    def _call(self, ctx: ProviderContext, method: str, path: str, data: dict | None = None,
              idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {ctx.credentials['secret_key']}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(method, f"{API}{path}", data=data, headers=headers)

    def create(self, ctx, intent, attempt, method_data) -> CreateResult:
        pm_type = "card" if attempt.method == "card" else BANK_DEBIT_BY_CURRENCY[intent.currency]
        form = {
            "mode": "payment",
            "payment_method_types[0]": pm_type,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": intent.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(intent.amount),
            "line_items[0][price_data][product_data][name]": intent.description or "Payment",
            "client_reference_id": intent.id,
            "metadata[sangam_payment_intent]": intent.id,
            "payment_intent_data[metadata][sangam_payment_intent]": intent.id,
            "success_url": ctx.return_url(intent, attempt),
            "cancel_url": ctx.cancel_url(intent, attempt),
        }
        if intent.customer.get("email"):
            form["customer_email"] = intent.customer["email"]
        session = self._call(ctx, "POST", "/checkout/sessions", form, idempotency_key=attempt.id)
        return CreateResult(provider_ref=session["id"], next_action={"type": "redirect", "url": session["url"]})

    @staticmethod
    def _status_from_session(session: dict) -> str:
        if session.get("payment_status") in ("paid", "no_payment_required"):
            return "succeeded"
        if session.get("status") == "complete":
            return "processing"  # bank debits clear in 1-4 business days
        if session.get("status") == "expired":
            return "failed"
        return "requires_action"

    def sync(self, ctx, intent, attempt, params) -> SyncResult:
        session = self._call(ctx, "GET", f"/checkout/sessions/{attempt.provider_ref}")
        status = self._status_from_session(session)
        if params.get("canceled") and status == "requires_action":
            return SyncResult(status="failed", failure_reason="Customer canceled at Stripe")
        return SyncResult(status=status, data={"stripe_payment_intent": session.get("payment_intent")})

    def refund(self, ctx, intent, attempt, amount) -> RefundResult:
        pi = attempt.data.get("stripe_payment_intent")
        if not pi:
            pi = self._call(ctx, "GET", f"/checkout/sessions/{attempt.provider_ref}").get("payment_intent")
        if not pi:
            raise ProviderError("Stripe session has no payment to refund")
        refund = self._call(ctx, "POST", "/refunds", {"payment_intent": pi, "amount": str(amount)})
        return RefundResult(status="succeeded" if refund.get("status") == "succeeded" else "pending",
                            provider_ref=refund.get("id"))

    # ---- recurring: Stripe Billing via Checkout in subscription mode ------------------------
    mandate_methods = {"card", "bank_debit"}
    managed_billing = True
    supports_transfers = True

    def create_mandate(self, ctx, sub, plan, customer, return_url) -> MandateResult:
        pm_type = "card" if sub.mandate_method == "card" else BANK_DEBIT_BY_CURRENCY.get(plan.currency, "card")
        form = {
            "mode": "subscription",
            "payment_method_types[0]": pm_type,
            "line_items[0][quantity]": str(sub.quantity),
            "line_items[0][price_data][currency]": plan.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(plan.amount),
            "line_items[0][price_data][recurring][interval]": plan.interval,
            "line_items[0][price_data][recurring][interval_count]": str(plan.interval_count),
            "line_items[0][price_data][product_data][name]": plan.name,
            "subscription_data[metadata][sangam_subscription]": sub.id,
            "client_reference_id": sub.id,
            "success_url": return_url,
            "cancel_url": return_url + "?canceled=1",
        }
        if sub.trial_end:
            from ..util import utcnow
            days = max(1, (sub.trial_end - utcnow()).days)
            form["subscription_data[trial_period_days]"] = str(days)
        if customer and customer.email:
            form["customer_email"] = customer.email
        session = self._call(ctx, "POST", "/checkout/sessions", form, idempotency_key=f"mandate-{sub.id}")
        return MandateResult(provider_ref=session["id"], authorization_url=session["url"])

    def sync_mandate(self, ctx, sub, params) -> str:
        if sub.mandate_ref and sub.mandate_ref.startswith("cs_"):
            session = self._call(ctx, "GET", f"/checkout/sessions/{sub.mandate_ref}")
            if session.get("subscription"):
                sub.mandate_ref = session["subscription"]  # from now on we track the Stripe subscription
            else:
                return "revoked" if params.get("canceled") else "pending"
        stripe_sub = self._call(ctx, "GET", f"/subscriptions/{sub.mandate_ref}")
        return {"active": "active", "trialing": "active", "past_due": "active",
                "canceled": "revoked", "incomplete_expired": "revoked"}.get(stripe_sub.get("status"), "pending")

    def cancel_mandate(self, ctx, sub) -> None:
        if sub.mandate_ref and sub.mandate_ref.startswith("sub_"):
            self._call(ctx, "DELETE", f"/subscriptions/{sub.mandate_ref}")

    def transfer(self, ctx, intent, attempt, destination, amount) -> str:
        pi_id = attempt.provider_payment_id or attempt.data.get("stripe_payment_intent")
        pi = self._call(ctx, "GET", f"/payment_intents/{pi_id}")
        tr = self._call(ctx, "POST", "/transfers", {
            "amount": str(amount), "currency": intent.currency.lower(), "destination": destination,
            "source_transaction": pi["latest_charge"], "transfer_group": intent.id})
        return tr["id"]

    def submit_dispute_evidence(self, ctx, dispute, evidence) -> None:
        if dispute.provider_ref:
            form = {"submit": "true", "evidence[uncategorized_text]": evidence.get("summary", "")[:20000]}
            for k in ("customer_email_address", "product_description", "shipping_tracking_number"):
                if evidence.get(k):
                    form[f"evidence[{k}]"] = evidence[k]
            self._call(ctx, "POST", f"/disputes/{dispute.provider_ref}", form)

    def handle_webhook(self, ctx, headers, body) -> WebhookResult | None:
        secret = ctx.credentials.get("webhook_secret")
        header = headers.get("stripe-signature", "")
        if not secret:
            raise WebhookSignatureError("Configure webhook_secret for Stripe to accept webhooks")
        parts: dict[str, list[str]] = {}
        for item in header.split(","):
            k, _, v = item.partition("=")
            parts.setdefault(k.strip(), []).append(v.strip())
        try:
            ts = int(parts["t"][0])
        except (KeyError, ValueError) as exc:
            raise WebhookSignatureError("Malformed Stripe-Signature") from exc
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        if abs(time.time() - ts) > 300 or not any(hmac.compare_digest(expected, s) for s in parts.get("v1", [])):
            raise WebhookSignatureError("Stripe signature mismatch")

        event = json.loads(body)
        obj = event.get("data", {}).get("object", {})
        etype = event.get("type", "")

        if etype == "invoice.paid" and obj.get("subscription"):
            return WebhookResult(provider_ref=obj["subscription"], status="succeeded", kind="subscription_charge",
                                 data={"amount": obj.get("amount_paid"), "payment_id": obj.get("payment_intent")})
        if etype == "invoice.payment_failed" and obj.get("subscription"):
            return WebhookResult(provider_ref=obj["subscription"], status="failed", kind="subscription_failed")
        if etype == "customer.subscription.deleted":
            return WebhookResult(provider_ref=obj["id"], status="revoked", kind="mandate")
        if etype.startswith("charge.dispute."):
            status = {"needs_response": "needs_response", "warning_needs_response": "needs_response",
                      "under_review": "under_review", "warning_under_review": "under_review",
                      "won": "won", "lost": "lost", "warning_closed": "won"}.get(obj.get("status"), "needs_response")
            due = (obj.get("evidence_details") or {}).get("due_by")
            return WebhookResult(provider_ref=obj["id"], status=status, kind="dispute",
                                 data={"payment_id": obj.get("payment_intent"), "amount": obj.get("amount"),
                                       "reason": obj.get("reason", "general"), "due_by": due})
        if etype == "checkout.session.completed" and obj.get("mode") == "subscription":
            return WebhookResult(provider_ref=obj["id"], status="active", kind="mandate_session",
                                 data={"subscription": obj.get("subscription")})

        mapping = {
            "checkout.session.completed": self._status_from_session(obj),
            "checkout.session.async_payment_succeeded": "succeeded",
            "checkout.session.async_payment_failed": "failed",
            "checkout.session.expired": "failed",
        }
        if event.get("type") not in mapping:
            return None
        return WebhookResult(provider_ref=obj["id"], status=mapping[event["type"]])
