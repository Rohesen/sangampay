"""RazorpayX payouts from the merchant's own RazorpayX current account: vendor payments, refunds to
bank accounts, seller settlements. Bank details are decrypted from the vault only at the moment of
the payout call and are sent straight to RazorpayX."""
from __future__ import annotations

import hashlib
import hmac
import json

from .base import Provider, ProviderError, WebhookResult, WebhookSignatureError

API = "https://api.razorpay.com/v1"


class RazorpayXProvider(Provider):
    name = "razorpayx"
    display_name = "RazorpayX Payouts"
    methods: set[str] = set()  # payouts only, never used for collecting money
    currencies = {"INR"}
    credential_fields = ["key_id", "key_secret"]
    config_fields = ["account_number"]  # your RazorpayX current/virtual account number
    supports_payouts = True

    def _call(self, ctx, method, path, payload=None, idem=None) -> dict:
        headers = {"X-Payout-Idempotency": idem} if idem else {}
        return self._request(method, f"{API}{path}", auth=(ctx.credentials["key_id"], ctx.credentials["key_secret"]),
                             json=payload, headers=headers)

    def payout(self, ctx, payout, bank) -> tuple[str, str]:
        contact = self._call(ctx, "POST", "/contacts", {"name": bank["account_holder_name"][:50], "type": "vendor",
                                                        "reference_id": payout.bank_account_id})
        if "vpa" in bank:
            fund = {"account_type": "vpa", "vpa": {"address": bank["vpa"]}}
        elif "ifsc" in bank:
            fund = {"account_type": "bank_account", "bank_account": {
                "name": bank["account_holder_name"][:50], "ifsc": bank["ifsc"], "account_number": bank["account_number"]}}
        else:
            raise ProviderError("RazorpayX pays out to Indian bank accounts and UPI IDs only")
        fa = self._call(ctx, "POST", "/fund_accounts", {"contact_id": contact["id"], **fund})
        res = self._call(ctx, "POST", "/payouts", {
            "account_number": ctx.config["account_number"], "fund_account_id": fa["id"], "amount": payout.amount,
            "currency": "INR", "mode": payout.transfer_mode, "purpose": payout.purpose,
            "queue_if_low_balance": True, "reference_id": payout.id, "narration": (payout.narration or "")[:30] or None,
        }, idem=payout.id)
        status = {"processed": "processed", "reversed": "reversed", "rejected": "failed", "failed": "failed",
                  "cancelled": "failed"}.get(res.get("status"), "processing")
        return status, res["id"]

    def handle_webhook(self, ctx, headers, body) -> WebhookResult | None:
        secret = ctx.credentials.get("webhook_secret")
        if not secret:
            raise WebhookSignatureError("Configure webhook_secret for RazorpayX to accept webhooks")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-razorpay-signature", "")):
            raise WebhookSignatureError("RazorpayX signature mismatch")
        event = json.loads(body)
        status = {"payout.processed": "processed", "payout.reversed": "reversed", "payout.failed": "failed",
                  "payout.rejected": "failed"}.get(event.get("event"))
        if not status:
            return None
        entity = event["payload"]["payout"]["entity"]
        return WebhookResult(provider_ref=entity["id"], status=status, kind="payout",
                             data={"reason": (entity.get("status_details") or {}).get("description")})
