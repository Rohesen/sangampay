"""Shopify bridge and SDK checks that need no network access to Shopify."""
import base64
import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load("shopify_bridge", ROOT / "integrations" / "shopify" / "bridge.py")
sdk = _load("sangampay_sdk", ROOT / "sdks" / "python" / "sangampay" / "__init__.py")


def test_shopify_signature_and_amounts():
    body = b'{"id": 1}'
    good = base64.b64encode(hmac.new(b"shh", body, hashlib.sha256).digest()).decode()
    assert bridge.verify_shopify(body, good, "shh") and not bridge.verify_shopify(body, "x", "shh")
    assert bridge.to_minor("1299.50", "INR") == 129950 and bridge.to_minor("1200", "JPY") == 1200


def test_sdk_and_bridge_verify_server_signatures():
    from sangam.security import sign
    body = json.dumps({"type": "payment_intent.succeeded", "data": {"id": "pi_x"}}).encode()
    header = sign("whsec_t", body)
    assert sdk.verify_webhook(body, header, "whsec_t")["data"]["id"] == "pi_x"
    assert bridge.verify_sangam(body, header, "whsec_t")
    old = sign("whsec_t", body, timestamp=int(time.time()) - 900)
    try:
        sdk.verify_webhook(body, old, "whsec_t")
        raise AssertionError("expected replay rejection")
    except sdk.WebhookVerificationError:
        pass
