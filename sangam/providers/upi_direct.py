"""Zero-fee UPI straight to the merchant's own UPI ID (VPA).

The customer scans a QR / taps a upi:// intent link, and money lands in the merchant's bank
account instantly via NPCI. No gateway sits in between, so there is no MDR.

Trade-off: without a licensed PSP there is no automatic payment confirmation. The customer
submits the 12-digit UTR from their UPI app and the merchant (or a bank-statement
reconciliation job) verifies it. For automatic confirmation route UPI through `razorpay`.
"""
from __future__ import annotations

from urllib.parse import urlencode, quote

from ..util import to_major
from ..vault import is_valid_vpa
from .base import CreateResult, Provider, ProviderContext, RefundResult


class UpiDirectProvider(Provider):
    name = "upi_direct"
    display_name = "UPI (direct to your UPI ID)"
    methods = {"upi"}
    currencies = {"INR"}
    config_fields = ["vpa", "payee_name"]

    def validate_setup(self, credentials, config) -> None:
        super().validate_setup(credentials, config)
        if not is_valid_vpa(config["vpa"]):
            from ..util import APIError
            raise APIError(400, "invalid_vpa", "UPI ID must look like name@bank", "vpa")

    def create(self, ctx: ProviderContext, intent, attempt, method_data) -> CreateResult:
        params = {
            "pa": ctx.config["vpa"],
            "pn": ctx.config["payee_name"],
            "am": to_major(intent.amount, "INR"),
            "cu": "INR",
            "tr": intent.id,  # transaction reference shows up in the merchant's bank statement
            "tn": (intent.description or f"Payment {intent.id}")[:50],
        }
        if ctx.config.get("mcc"):
            params["mc"] = ctx.config["mcc"]
        uri = "upi://pay?" + urlencode(params, quote_via=quote)
        return CreateResult(provider_ref=intent.id, next_action={
            "type": "upi_intent", "uri": uri, "vpa": ctx.config["vpa"], "payee_name": ctx.config["payee_name"],
            "amount": params["am"], "instructions": "Pay with any UPI app, then enter the 12-digit UTR/reference number."})

    def refund(self, ctx, intent, attempt, amount) -> RefundResult:
        # UPI has no push-back API without a PSP; the merchant sends it from their UPI app/bank.
        return RefundResult(status="manual_action_required")
