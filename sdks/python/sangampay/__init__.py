"""SangamPay Python SDK. Zero dependencies (standard library only), Python 3.8+.

    from sangampay import SangamPay, verify_webhook

    sp = SangamPay("sk_test_...", base_url="https://pay.example.com")
    pi = sp.payment_intents.create(amount=49900, currency="INR", description="Order #1001",
                                   idempotency_key="order-1001")
    print(pi["checkout_url"])

    # in your webhook handler
    event = verify_webhook(raw_body, request.headers["Sangam-Signature"], "whsec_...")
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

__version__ = "1.0.0"
__all__ = ["SangamPay", "SangamPayError", "verify_webhook", "WebhookVerificationError"]


class SangamPayError(Exception):
    def __init__(self, status: int, code: str, message: str, param: Optional[str] = None):
        super().__init__(f"[{status} {code}] {message}")
        self.status, self.code, self.message, self.param = status, code, message, param


class WebhookVerificationError(Exception):
    pass


def verify_webhook(body: bytes, signature_header: str, secret: str, tolerance: int = 300) -> Dict[str, Any]:
    """Verify a Sangam-Signature header and return the parsed event. Raises WebhookVerificationError."""
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError) as exc:
        raise WebhookVerificationError("Malformed Sangam-Signature header") from exc
    if abs(time.time() - ts) > tolerance:
        raise WebhookVerificationError("Timestamp outside the tolerance window (possible replay)")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts.get("v1", "")):
        raise WebhookVerificationError("Signature mismatch")
    return json.loads(body)


class _Resource:
    def __init__(self, client: "SangamPay", path: str):
        self._c, self._p = client, path

    def create(self, idempotency_key: Optional[str] = None, **params: Any) -> Dict[str, Any]:
        return self._c.request("POST", self._p, params, idempotency_key=idempotency_key)

    def retrieve(self, obj_id: str) -> Dict[str, Any]:
        return self._c.request("GET", f"{self._p}/{obj_id}")

    def list(self, **query: Any) -> Dict[str, Any]:
        q = {k: v for k, v in query.items() if v is not None}
        return self._c.request("GET", self._p + (f"?{urllib.parse.urlencode(q)}" if q else ""))

    def action(self, obj_id: str, name: str, **params: Any) -> Dict[str, Any]:
        return self._c.request("POST", f"{self._p}/{obj_id}/{name}", params)


class _PaymentIntents(_Resource):
    def confirm(self, pi_id: str, method: str, **method_data: Any) -> Dict[str, Any]:
        return self.action(pi_id, "confirm", method=method, method_data=method_data)

    def cancel(self, pi_id: str) -> Dict[str, Any]:
        return self.action(pi_id, "cancel")

    def verify(self, pi_id: str, approve: bool = True, note: Optional[str] = None) -> Dict[str, Any]:
        return self.action(pi_id, "verify", approve=approve, note=note)


class _Subscriptions(_Resource):
    def cancel(self, sub_id: str, at_period_end: bool = False) -> Dict[str, Any]:
        return self.action(sub_id, "cancel", at_period_end=at_period_end)


class _Invoices(_Resource):
    def void(self, inv_id: str) -> Dict[str, Any]:
        return self.action(inv_id, "void")

    def mark_paid(self, inv_id: str, note: Optional[str] = None) -> Dict[str, Any]:
        return self.action(inv_id, "mark_paid", note=note)


class SangamPay:
    def __init__(self, secret_key: str, base_url: str = "http://localhost:8000", timeout: float = 30):
        self.secret_key, self.base_url, self.timeout = secret_key, base_url.rstrip("/"), timeout
        self.payment_intents = _PaymentIntents(self, "/v1/payment_intents")
        self.refunds = _Resource(self, "/v1/refunds")
        self.customers = _Resource(self, "/v1/customers")
        self.plans = _Resource(self, "/v1/plans")
        self.subscriptions = _Subscriptions(self, "/v1/subscriptions")
        self.invoices = _Invoices(self, "/v1/invoices")
        self.payment_links = _Resource(self, "/v1/payment_links")
        self.payouts = _Resource(self, "/v1/payouts")
        self.bank_accounts = _Resource(self, "/v1/bank_accounts")
        self.disputes = _Resource(self, "/v1/disputes")
        self.webhook_endpoints = _Resource(self, "/v1/webhook_endpoints")
        self.events = _Resource(self, "/v1/events")

    def reconcile(self, csv_text: str, auto_approve: bool = True) -> Dict[str, Any]:
        return self.request("POST", "/v1/reconciliation", {"csv": csv_text, "auto_approve": auto_approve})

    def analytics(self, days: int = 30) -> Dict[str, Any]:
        return self.request("GET", f"/v1/analytics?days={days}")

    def balance(self) -> Dict[str, Any]:
        return self.request("GET", "/v1/balance")

    def request(self, method: str, path: str, body: Optional[dict] = None,
                idempotency_key: Optional[str] = None, retries: int = 2) -> Dict[str, Any]:
        data = json.dumps({k: v for k, v in (body or {}).items() if v is not None}).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json",
                   "User-Agent": f"sangampay-python/{__version__}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read() or b"{}").get("error", {})
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt < retries and (method == "GET" or idempotency_key):
                    time.sleep(float(exc.headers.get("Retry-After", 2 ** attempt)))
                    continue
                raise SangamPayError(exc.code, payload.get("code", "error"), payload.get("message", str(exc)),
                                     payload.get("param")) from None
            except urllib.error.URLError:
                if attempt < retries and (method == "GET" or idempotency_key):
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")
