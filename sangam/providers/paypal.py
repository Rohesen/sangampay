"""PayPal Orders v2 using the merchant's own PayPal business account (200+ countries)."""
from __future__ import annotations

import json

from ..util import to_major
from .base import (CreateResult, Provider, ProviderContext, ProviderError, RefundResult, SyncResult,
                   WebhookResult, WebhookSignatureError)

# Currencies PayPal accepts for payments.
PAYPAL_CURRENCIES = {"AUD", "BRL", "CAD", "CNY", "CZK", "DKK", "EUR", "HKD", "HUF", "ILS", "JPY", "MYR",
                     "MXN", "TWD", "NZD", "NOK", "PHP", "PLN", "GBP", "SGD", "SEK", "CHF", "THB", "USD"}


class PayPalProvider(Provider):
    name = "paypal"
    display_name = "PayPal"
    methods = {"paypal"}
    currencies = PAYPAL_CURRENCIES
    credential_fields = ["client_id", "client_secret"]  # webhook_id optional, enables webhooks

    @staticmethod
    def _api(ctx: ProviderContext) -> str:
        return "https://api-m.paypal.com" if ctx.mode == "live" else "https://api-m.sandbox.paypal.com"

    def _token(self, ctx: ProviderContext) -> str:
        data = self._request("POST", f"{self._api(ctx)}/v1/oauth2/token",
                             auth=(ctx.credentials["client_id"], ctx.credentials["client_secret"]),
                             data={"grant_type": "client_credentials"})
        return data["access_token"]

    def _call(self, ctx, method, path, payload=None, request_id=None) -> dict:
        headers = {"Authorization": f"Bearer {self._token(ctx)}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # PayPal's idempotency key
        return self._request(method, f"{self._api(ctx)}{path}", headers=headers,
                             content=json.dumps(payload) if payload is not None else None)

    def _value(self, intent, amount: int) -> str:
        # PayPal does not allow decimals for HUF, JPY and TWD.
        value = to_major(amount, intent.currency)
        return value.split(".")[0] if intent.currency in ("HUF", "JPY", "TWD") else value

    def create(self, ctx, intent, attempt, method_data) -> CreateResult:
        order = self._call(ctx, "POST", "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": intent.id,
                "custom_id": intent.id,
                "description": (intent.description or "Payment")[:127],
                "amount": {"currency_code": intent.currency, "value": self._value(intent, intent.amount)},
            }],
            "payment_source": {"paypal": {"experience_context": {
                "return_url": ctx.return_url(intent, attempt),
                "cancel_url": ctx.cancel_url(intent, attempt),
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            }}},
        }, request_id=attempt.id)
        link = next((l["href"] for l in order.get("links", []) if l["rel"] in ("payer-action", "approve")), None)
        if not link:
            raise ProviderError("PayPal did not return an approval link")
        return CreateResult(provider_ref=order["id"], next_action={"type": "redirect", "url": link})

    def sync(self, ctx, intent, attempt, params) -> SyncResult:
        if params.get("canceled"):
            return SyncResult(status="failed", failure_reason="Customer canceled at PayPal")
        order = self._call(ctx, "GET", f"/v2/checkout/orders/{attempt.provider_ref}")
        if order.get("status") == "APPROVED":
            order = self._call(ctx, "POST", f"/v2/checkout/orders/{attempt.provider_ref}/capture", {},
                               request_id=f"{attempt.id}-capture")
        if order.get("status") != "COMPLETED":
            return SyncResult(status="requires_action")
        capture = order["purchase_units"][0]["payments"]["captures"][0]
        status = {"COMPLETED": "succeeded", "PENDING": "processing"}.get(capture["status"], "failed")
        return SyncResult(status=status, data={"paypal_capture_id": capture["id"]})

    def refund(self, ctx, intent, attempt, amount) -> RefundResult:
        capture_id = attempt.data.get("paypal_capture_id")
        if not capture_id:
            raise ProviderError("No PayPal capture recorded for this payment")
        refund = self._call(ctx, "POST", f"/v2/payments/captures/{capture_id}/refund", {
            "amount": {"currency_code": intent.currency, "value": self._value(intent, amount)}})
        return RefundResult(status="succeeded" if refund.get("status") == "COMPLETED" else "pending",
                            provider_ref=refund.get("id"))

    def handle_webhook(self, ctx, headers, body) -> WebhookResult | None:
        webhook_id = ctx.credentials.get("webhook_id")
        if not webhook_id:
            raise WebhookSignatureError("Configure webhook_id for PayPal to accept webhooks")
        event = json.loads(body)
        check = self._call(ctx, "POST", "/v1/notifications/verify-webhook-signature", {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        })
        if check.get("verification_status") != "SUCCESS":
            raise WebhookSignatureError("PayPal signature verification failed")
        resource = event.get("resource", {})
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        status = {"PAYMENT.CAPTURE.COMPLETED": "succeeded", "PAYMENT.CAPTURE.DENIED": "failed",
                  "PAYMENT.CAPTURE.DECLINED": "failed"}.get(event.get("event_type"))
        if not order_id or not status:
            return None
        return WebhookResult(provider_ref=order_id, status=status)
