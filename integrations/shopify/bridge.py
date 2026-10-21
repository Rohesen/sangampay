"""SangamPay ⇄ Shopify bridge (standard library only).

Shopify lets only approved partners build payment-gateway apps, so this bridge uses Shopify's
supported "manual payment method" flow instead:

  1. Customer checks out with a manual payment method, e.g. "UPI / Cards / Net banking (SangamPay)".
  2. Shopify sends an orders/create webhook here; the bridge creates a SangamPay payment and writes
     the pay link onto the order (note + "Pay now" attribute you can show in the confirmation email).
  3. When SangamPay reports payment_intent.succeeded, the bridge marks the Shopify order as paid.

Environment:
  SHOPIFY_SHOP=your-store.myshopify.com   SHOPIFY_ADMIN_TOKEN=shpat_...   SHOPIFY_WEBHOOK_SECRET=...
  SHOPIFY_PAYMENT_METHOD="UPI / Cards / Net banking (SangamPay)"
  SANGAM_URL=https://pay.example.com      SANGAM_SECRET_KEY=sk_live_...   SANGAM_WEBHOOK_SECRET=whsec_...

Run:   python bridge.py --port 8787
Routes: POST /shopify/orders-create   POST /sangam/webhook
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("sangam.shopify")
API_VERSION = "2024-10"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing environment variable {name}")
    return value


def verify_shopify(body: bytes, header: str, secret: str) -> bool:
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, header or "")


def verify_sangam(body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def http_json(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json", **headers},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def to_minor(amount: str, currency: str) -> int:
    exp = 0 if currency in ("JPY", "KRW", "VND", "CLP", "ISK") else 3 if currency in ("BHD", "KWD", "OMR", "JOD", "TND") else 2
    return int((Decimal(amount) * 10 ** exp).to_integral_value())


class Bridge:
    def __init__(self) -> None:
        self.shop, self.token = env("SHOPIFY_SHOP"), env("SHOPIFY_ADMIN_TOKEN")
        self.shopify_secret = env("SHOPIFY_WEBHOOK_SECRET")
        self.method_name = os.environ.get("SHOPIFY_PAYMENT_METHOD", "SangamPay").lower()
        self.sangam_url, self.sangam_key = env("SANGAM_URL").rstrip("/"), env("SANGAM_SECRET_KEY")
        self.sangam_secret = env("SANGAM_WEBHOOK_SECRET")

    def shopify(self, method: str, path: str, body: dict | None = None) -> dict:
        return http_json(method, f"https://{self.shop}/admin/api/{API_VERSION}{path}",
                         {"X-Shopify-Access-Token": self.token}, body)

    def on_order(self, order: dict) -> str:
        gateways = " ".join(order.get("payment_gateway_names") or []).lower()
        if self.method_name not in gateways or order.get("financial_status") not in ("pending", "partially_paid"):
            return "skipped"
        currency = order["currency"]
        amount = to_minor(order.get("total_outstanding") or order["total_price"], currency)
        pi = http_json("POST", f"{self.sangam_url}/v1/payment_intents",
                       {"Authorization": f"Bearer {self.sangam_key}", "Idempotency-Key": f"shopify-{order['id']}"},
                       {"amount": amount, "currency": currency, "description": f"Order {order.get('name', order['id'])}",
                        "customer": {k: v for k, v in {"email": order.get("email"), "phone": order.get("phone")}.items() if v},
                        "return_url": order.get("order_status_url"),
                        "metadata": {"shopify_order_id": str(order["id"]), "shopify_order_name": order.get("name", "")}})
        note = ((order.get("note") or "") + f"\nPay online: {pi['checkout_url']}").strip()
        self.shopify("PUT", f"/orders/{order['id']}.json", {"order": {
            "id": order["id"], "note": note,
            "note_attributes": (order.get("note_attributes") or []) + [{"name": "Pay now", "value": pi["checkout_url"]},
                                                                      {"name": "SangamPay payment", "value": pi["id"]}]}})
        return pi["id"]

    def on_sangam_event(self, event: dict) -> str:
        if event.get("type") != "payment_intent.succeeded":
            return "ignored"
        order_id = (event["data"].get("metadata") or {}).get("shopify_order_id")
        if not order_id:
            return "ignored"
        res = self.shopify("POST", "/graphql.json", {
            "query": "mutation($input: OrderMarkAsPaidInput!){orderMarkAsPaid(input:$input){userErrors{field message}}}",
            "variables": {"input": {"id": f"gid://shopify/Order/{order_id}"}}})
        errors = res.get("data", {}).get("orderMarkAsPaid", {}).get("userErrors") or []
        if errors:
            log.warning("Shopify refused orderMarkAsPaid for %s: %s", order_id, errors)
        return "marked_paid"


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                if self.path == "/shopify/orders-create":
                    if not verify_shopify(body, self.headers.get("X-Shopify-Hmac-Sha256"), bridge.shopify_secret):
                        return self._reply(401, "bad signature")
                    return self._reply(200, bridge.on_order(json.loads(body)))
                if self.path == "/sangam/webhook":
                    if not verify_sangam(body, self.headers.get("Sangam-Signature"), bridge.sangam_secret):
                        return self._reply(401, "bad signature")
                    return self._reply(200, bridge.on_sangam_event(json.loads(body)))
                return self._reply(404, "not found")
            except Exception as exc:  # report 500 so the sender retries
                log.exception("bridge error")
                return self._reply(500, str(exc)[:200])

        def _reply(self, code: int, text: str):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(text.encode())

    return Handler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(Bridge()))
    log.info("SangamPay ⇄ Shopify bridge listening on :%s", args.port)
    server.serve_forever()
