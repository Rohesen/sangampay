"""Razorpay (merchant's own account) for Indian customers: net banking (pay from bank account), UPI
with automatic confirmation, cards, recurring UPI AutoPay / e-NACH / card mandates, Route split
payments, and disputes. Bank logins and card numbers are entered on Razorpay/the bank's pages.

Any RBI-authorised payment aggregator (Cashfree, PayU, Juspay...) can be added the same way."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timezone

from .base import (CreateResult, MandateResult, Provider, ProviderError, RefundResult, SyncResult,
                   WebhookResult, WebhookSignatureError)

API = "https://api.razorpay.com/v1"
PERIODS = {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}


class RazorpayProvider(Provider):
    name = "razorpay"
    display_name = "Razorpay"
    methods = {"upi", "netbanking", "card"}
    currencies = {"INR"}
    credential_fields = ["key_id", "key_secret"]  # webhook_secret optional
    mandate_methods = {"upi", "emandate", "card"}
    managed_billing = True
    supports_transfers = True

    def _call(self, ctx, method, path, payload=None) -> dict:
        return self._request(method, f"{API}{path}",
                             auth=(ctx.credentials["key_id"], ctx.credentials["key_secret"]),
                             json=payload)

    # ---- one-time payments ---------------------------------------------------------------
    def create(self, ctx, intent, attempt, method_data) -> CreateResult:
        only = {m: (m == attempt.method) for m in ("upi", "netbanking", "card", "wallet")}
        payload = {
            "amount": intent.amount,
            "currency": intent.currency,
            "reference_id": attempt.id,  # must be unique per link
            "description": (intent.description or f"Payment {intent.id}")[:2048],
            "callback_url": ctx.return_url(intent, attempt),
            "callback_method": "get",
            "notify": {"sms": False, "email": False},
            "notes": {"sangam_payment_intent": intent.id},
            "options": {"checkout": {"method": only}},
        }
        if intent.customer.get("email"):
            payload["customer"] = {"email": intent.customer["email"]}
        link = self._call(ctx, "POST", "/payment_links", payload)
        return CreateResult(provider_ref=link["id"], next_action={"type": "redirect", "url": link["short_url"]})

    def sync(self, ctx, intent, attempt, params) -> SyncResult:
        link = self._call(ctx, "GET", f"/payment_links/{attempt.provider_ref}")
        status = {"paid": "succeeded", "cancelled": "failed", "expired": "failed"}.get(link.get("status"),
                                                                                      "requires_action")
        payments = link.get("payments") or []
        data = {"razorpay_payment_id": payments[-1]["payment_id"]} if payments else {}
        return SyncResult(status=status, data=data)

    def refund(self, ctx, intent, attempt, amount) -> RefundResult:
        payment_id = attempt.provider_payment_id or attempt.data.get("razorpay_payment_id")
        if not payment_id:
            raise ProviderError("No Razorpay payment recorded for this link")
        refund = self._call(ctx, "POST", f"/payments/{payment_id}/refund", {"amount": amount})
        return RefundResult(status="succeeded" if refund.get("status") == "processed" else "pending",
                            provider_ref=refund.get("id"))

    # ---- recurring (UPI AutoPay, e-NACH, card mandates) -------------------------------------
    def create_mandate(self, ctx, sub, plan, customer, return_url) -> MandateResult:
        plan_id = (plan.provider_refs or {}).get(f"razorpay:{ctx.account_id}")
        if not plan_id:
            rp_plan = self._call(ctx, "POST", "/plans", {
                "period": PERIODS[plan.interval], "interval": plan.interval_count,
                "item": {"name": plan.name, "amount": plan.amount, "currency": plan.currency}})
            plan_id = rp_plan["id"]
            plan.provider_refs = {**(plan.provider_refs or {}), f"razorpay:{ctx.account_id}": plan_id}
        payload = {"plan_id": plan_id, "total_count": 120, "quantity": sub.quantity, "customer_notify": 1,
                   "notes": {"sangam_subscription": sub.id}}
        if sub.trial_end:
            payload["start_at"] = int(sub.trial_end.replace(tzinfo=timezone.utc).timestamp())
        rp_sub = self._call(ctx, "POST", "/subscriptions", payload)
        return MandateResult(provider_ref=rp_sub["id"], authorization_url=rp_sub["short_url"])

    def sync_mandate(self, ctx, sub, params) -> str:
        rp_sub = self._call(ctx, "GET", f"/subscriptions/{sub.mandate_ref}")
        return {"authenticated": "active", "active": "active", "cancelled": "revoked", "halted": "revoked",
                "completed": "revoked", "expired": "revoked"}.get(rp_sub.get("status"), "pending")

    def cancel_mandate(self, ctx, sub) -> None:
        if sub.mandate_ref:
            self._call(ctx, "POST", f"/subscriptions/{sub.mandate_ref}/cancel", {"cancel_at_cycle_end": 0})

    # ---- Route (marketplace splits) ----------------------------------------------------------
    def transfer(self, ctx, intent, attempt, destination, amount) -> str:
        payment_id = attempt.provider_payment_id or attempt.data.get("razorpay_payment_id")
        if not payment_id:
            raise ProviderError("No Razorpay payment to split")
        res = self._call(ctx, "POST", f"/payments/{payment_id}/transfers", {
            "transfers": [{"account": destination, "amount": amount, "currency": intent.currency}]})
        return res["items"][0]["id"]

    def submit_dispute_evidence(self, ctx, dispute, evidence) -> None:
        if dispute.provider_ref:
            self._call(ctx, "PATCH", f"/disputes/{dispute.provider_ref}/contest",
                       {"summary": evidence.get("summary", "")[:1000], "action": "submit"})

    # ---- webhooks -------------------------------------------------------------------------
    def handle_webhook(self, ctx, headers, body) -> WebhookResult | None:
        secret = ctx.credentials.get("webhook_secret")
        if not secret:
            raise WebhookSignatureError("Configure webhook_secret for Razorpay to accept webhooks")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-razorpay-signature", "")):
            raise WebhookSignatureError("Razorpay signature mismatch")
        event = json.loads(body)
        name, payload = event.get("event", ""), event.get("payload", {})

        link_status = {"payment_link.paid": "succeeded", "payment_link.cancelled": "failed",
                       "payment_link.expired": "failed"}.get(name)
        if link_status:
            return WebhookResult(provider_ref=payload["payment_link"]["entity"]["id"], status=link_status)

        if name.startswith("subscription."):
            sub = payload["subscription"]["entity"]
            if name == "subscription.charged":
                pay = payload.get("payment", {}).get("entity", {})
                return WebhookResult(provider_ref=sub["id"], status="succeeded", kind="subscription_charge",
                                     data={"amount": pay.get("amount"), "payment_id": pay.get("id")})
            if name in ("subscription.authenticated", "subscription.activated"):
                return WebhookResult(provider_ref=sub["id"], status="active", kind="mandate")
            if name == "subscription.pending":
                return WebhookResult(provider_ref=sub["id"], status="failed", kind="subscription_failed")
            if name in ("subscription.halted", "subscription.cancelled", "subscription.completed"):
                return WebhookResult(provider_ref=sub["id"], status="revoked", kind="mandate")
            return None

        if name.startswith("payment.dispute."):
            d = payload["dispute"]["entity"]
            status = {"payment.dispute.created": "needs_response", "payment.dispute.under_review": "under_review",
                      "payment.dispute.action_required": "needs_response", "payment.dispute.won": "won",
                      "payment.dispute.lost": "lost", "payment.dispute.closed": "won"}.get(name, "needs_response")
            return WebhookResult(provider_ref=d["id"], status=status, kind="dispute",
                                 data={"payment_id": d.get("payment_id"), "amount": d.get("amount"),
                                       "reason": d.get("reason_code", "general"), "due_by": d.get("respond_by")})
        return None
