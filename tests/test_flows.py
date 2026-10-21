import hashlib
import hmac
import json
import time

import httpx
import pytest

from sangam import vault
from sangam.db import SessionLocal
from sangam.models import BankAccount, ProviderAccount, WebhookDelivery
from sangam.security import sign, verify
from sangam.webhooks import dispatch_due


def new_intent(client, h, **kw):
    body = {"amount": 49900, "currency": "INR", "description": "Masala chai x10", **kw}
    r = client.post("/v1/payment_intents", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def cs_header(pi):
    return {"X-Client-Secret": pi["client_secret"]}


def test_auth_required(client):
    assert client.get("/v1/payment_intents").status_code == 401
    assert client.get("/v1/payment_intents", headers={"Authorization": "Bearer sk_test_nope"}).status_code == 401


def test_sandbox_redirect_flow_and_ledger(client, merchant):
    pi = new_intent(client, merchant["h"])
    assert pi["status"] == "requires_payment_method"
    assert "#cs=" in pi["checkout_url"]

    view = client.get(f"/v1/checkout/{pi['id']}", headers=cs_header(pi)).json()
    assert {m["method"] for m in view["available_methods"]} >= {"upi", "netbanking", "card"}
    assert client.get(f"/v1/checkout/{pi['id']}", headers={"X-Client-Secret": "wrong"}).status_code == 401

    r = client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "netbanking"}, headers=cs_header(pi))
    action = r.json()["next_action"]
    assert action["type"] == "redirect" and "/sandbox/mock/" in action["url"]

    attempt_id = action["url"].rsplit("/", 1)[1]
    r = client.post(f"/sandbox/mock/{attempt_id}/approve", follow_redirects=True)
    assert "Payment successful" in r.text

    got = client.get(f"/v1/payment_intents/{pi['id']}", headers=merchant["h"]).json()
    assert got["status"] == "succeeded" and got["provider"] == "mock"
    bal = client.get("/v1/balance", headers=merchant["h"]).json()["data"]
    assert bal == [{"currency": "INR", "gross_volume": 49900, "refunded": 0, "disputes_lost": 0, "net_volume": 49900,
                    "payouts_sent": 0, "by_provider": {"mock": 49900}}]


def test_decline_then_retry(client, merchant):
    pi = new_intent(client, merchant["h"])
    client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "card", "method_data": {"simulate": "fail"}},
                headers=cs_header(pi))
    view = client.get(f"/v1/checkout/{pi['id']}", headers=cs_header(pi)).json()
    assert view["status"] == "requires_payment_method" and view["failure_reason"]
    view = client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "upi", "method_data": {"simulate": "success"}},
                       headers=cs_header(pi)).json()
    assert view["status"] == "succeeded"


def test_upi_direct_utr_verification_and_manual_refund(client, merchant):
    h = merchant["h"]
    r = client.put("/v1/providers/upi_direct", json={"config": {"vpa": "chaipoint@okhdfcbank", "payee_name": "Chai Point"}},
                   headers=h)
    assert r.status_code == 200, r.text
    client.put("/v1/merchant/routing", json={"routing": {"upi": ["upi_direct"]}}, headers=h)

    pi = new_intent(client, h)
    view = client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "upi"}, headers=cs_header(pi)).json()
    action = view["next_action"]
    assert action["type"] == "upi_intent"
    assert action["uri"].startswith("upi://pay?pa=chaipoint%40okhdfcbank") and "am=499.00" in action["uri"]

    bad = client.post(f"/v1/checkout/{pi['id']}/utr", json={"utr": "123"}, headers=cs_header(pi))
    assert bad.status_code == 400
    view = client.post(f"/v1/checkout/{pi['id']}/utr", json={"utr": "412345678901"}, headers=cs_header(pi)).json()
    assert view["status"] == "pending_verification"

    # Same UTR cannot be reused to "pay" a second order.
    pi2 = new_intent(client, h)
    client.post(f"/v1/checkout/{pi2['id']}/confirm", json={"method": "upi"}, headers=cs_header(pi2))
    dup = client.post(f"/v1/checkout/{pi2['id']}/utr", json={"utr": "412345678901"}, headers=cs_header(pi2))
    assert dup.status_code == 409

    got = client.post(f"/v1/payment_intents/{pi['id']}/verify", json={"approve": True}, headers=h).json()
    assert got["status"] == "succeeded" and got["provider"] == "upi_direct"

    rf = client.post("/v1/refunds", json={"payment_intent": pi["id"], "amount": 10000}, headers=h).json()
    assert rf["status"] == "manual_action_required"
    assert client.get(f"/v1/payment_intents/{pi['id']}", headers=h).json()["status"] == "partially_refunded"
    done = client.post(f"/v1/refunds/{rf['id']}/mark_completed", json={"reference": "UTR998877665544"}, headers=h).json()
    assert done["status"] == "succeeded"


def test_failover_to_next_provider(client, merchant):
    h = merchant["h"]
    pi = new_intent(client, h)
    # mock raises on the first try; there is no other provider so routing reports the failure clearly
    r = client.post(f"/v1/payment_intents/{pi['id']}/confirm", json={"method": "card", "method_data": {"simulate": "provider_down"}},
                    headers=h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "providers_unavailable"

    # Put a real-but-broken gateway first; routing must fall over to the sandbox.
    client.put("/v1/providers/stripe", json={"credentials": {"secret_key": "sk_test_invalid"}}, headers=h)
    import sangam.providers.stripe as stripe_mod

    def boom(*a, **kw):
        raise stripe_mod.ProviderError("stripe unreachable")
    original = stripe_mod.StripeProvider._call
    stripe_mod.StripeProvider._call = boom
    try:
        got = client.post(f"/v1/payment_intents/{pi['id']}/confirm", json={"method": "card", "method_data": {"simulate": "success"}},
                          headers=h).json()
    finally:
        stripe_mod.StripeProvider._call = original
    assert got["status"] == "succeeded" and got["provider"] == "mock"
    attempts = client.get(f"/v1/payment_intents/{pi['id']}/attempts", headers=h).json()["data"]
    assert [a["provider"] for a in attempts][-2:] == ["stripe", "mock"]
    assert attempts[-2]["status"] == "failed"


def test_idempotency(client, merchant):
    h = {**merchant["h"], "Idempotency-Key": "order-1001"}
    body = {"amount": 1000, "currency": "USD"}
    a = client.post("/v1/payment_intents", json=body, headers=h)
    b = client.post("/v1/payment_intents", json=body, headers=h)
    assert a.json()["id"] == b.json()["id"] and b.headers.get("Idempotent-Replayed") == "true"
    c = client.post("/v1/payment_intents", json={"amount": 2000, "currency": "USD"}, headers=h)
    assert c.status_code == 422


def test_refund_limits(client, merchant):
    h = merchant["h"]
    pi = new_intent(client, h, amount=1000)
    client.post(f"/v1/payment_intents/{pi['id']}/confirm", json={"method": "card", "method_data": {"simulate": "success"}},
                headers=h)
    assert client.post("/v1/refunds", json={"payment_intent": pi["id"], "amount": 1001}, headers=h).status_code == 400
    assert client.post("/v1/refunds", json={"payment_intent": pi["id"], "amount": 400}, headers=h).json()["status"] == "succeeded"
    assert client.post("/v1/refunds", json={"payment_intent": pi["id"]}, headers=h).json()["amount"] == 600
    assert client.get(f"/v1/payment_intents/{pi['id']}", headers=h).json()["status"] == "refunded"


def test_test_and_live_are_isolated(client, merchant):
    pi = new_intent(client, merchant["h"])
    live = {"Authorization": f"Bearer {merchant['live']}"}
    assert client.get(f"/v1/payment_intents/{pi['id']}", headers=live).status_code == 404
    live_pi = new_intent(client, live)
    view = client.get(f"/v1/checkout/{live_pi['id']}", headers=cs_header(live_pi)).json()
    assert view["available_methods"] == []  # no sandbox in live mode, no gateways connected yet


def test_bank_vault_encrypts_and_masks(client, merchant):
    h = merchant["h"]
    r = client.post("/v1/bank_accounts", json={"kind": "in_bank", "owner": "customer", "customer_ref": "cust_42",
                                               "details": {"account_holder_name": "Asha Rao",
                                                           "account_number": "50100123456789", "ifsc": "hdfc0001234"}},
                    headers=h)
    assert r.status_code == 201, r.text
    ba = r.json()
    assert ba["details"]["account_number"] == "XXXXXXXXXX6789" and ba["details"]["ifsc"] == "HDFC0001234"
    assert "50100123456789" not in json.dumps(client.get("/v1/bank_accounts", headers=h).json())

    with SessionLocal() as db:
        row = db.get(BankAccount, ba["id"])
        assert "50100123456789" not in row.enc_data
        assert vault.decrypt(row.enc_data, f"bank:{row.merchant_id}:{row.id}")["account_number"] == "50100123456789"
        with pytest.raises(vault.VaultError):  # ciphertext is bound to its owner
            vault.decrypt(row.enc_data, f"bank:acct_someoneelse:{row.id}")

    again = client.post("/v1/bank_accounts", json={"kind": "in_bank", "details": {
        "account_holder_name": "Asha Rao", "account_number": "5010 0123 4567 89", "ifsc": "HDFC0001234"}}, headers=h)
    assert again.status_code == 200 and again.json()["id"] == ba["id"]  # de-duplicated by fingerprint

    for kind, details, code in [
        ("in_bank", {"account_holder_name": "A", "account_number": "12", "ifsc": "HDFC0001234"}, "invalid_account_number"),
        ("in_bank", {"account_holder_name": "A", "account_number": "123456789", "ifsc": "BAD"}, "invalid_ifsc"),
        ("iban", {"account_holder_name": "A", "iban": "DE89370400440532013001"}, "invalid_iban"),
        ("us_ach", {"account_holder_name": "A", "account_number": "1234567", "routing_number": "123456789"}, "invalid_routing_number"),
    ]:
        r = client.post("/v1/bank_accounts", json={"kind": kind, "details": details}, headers=h)
        assert r.json()["error"]["code"] == code
    ok = client.post("/v1/bank_accounts", json={"kind": "iban", "details": {
        "account_holder_name": "Jonas", "iban": "DE89 3704 0044 0532 0130 00"}}, headers=h)
    assert ok.status_code == 201 and ok.json()["details"]["iban"].startswith("DE89")


def test_provider_credentials_are_encrypted(client, merchant):
    h = merchant["h"]
    r = client.put("/v1/providers/razorpay", json={"credentials": {"key_id": "rzp_test_abc", "key_secret": "supersecretvalue"}},
                   headers=h).json()
    assert r["credentials"]["key_secret"].endswith("alue") and "supersecret" not in r["credentials"]["key_secret"]
    with SessionLocal() as db:
        acct = db.query(ProviderAccount).filter_by(provider="razorpay").order_by(ProviderAccount.created_at.desc()).first()
        assert "supersecretvalue" not in acct.enc_credentials


def test_outgoing_webhooks_are_signed_and_retried(client, merchant):
    h = merchant["h"]
    ep = client.post("/v1/webhook_endpoints", json={"url": "https://merchant.example/hooks", "events": ["payment_intent.succeeded"]},
                     headers=h).json()
    pi = new_intent(client, h)
    client.post(f"/v1/payment_intents/{pi['id']}/confirm", json={"method": "upi", "method_data": {"simulate": "success"}},
                headers=h)

    seen = []
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        seen.append(request)
        return httpx.Response(200)

    fake = httpx.Client(transport=httpx.MockTransport(handler))
    with SessionLocal() as db:
        assert dispatch_due(db, client=fake) == 1  # first try: HTTP 500 -> rescheduled
        d = db.query(WebhookDelivery).filter_by(endpoint_id=ep["id"]).one()
        assert d.status == "pending" and d.attempts == 1
        d.next_attempt_at = d.created_at  # pretend the backoff elapsed
        db.commit()
        dispatch_due(db, client=fake)
        db.refresh(d)
        assert d.status == "succeeded"

    req = seen[0]
    assert verify(ep["secret"], req.content, req.headers["Sangam-Signature"])
    assert not verify("whsec_wrong", req.content, req.headers["Sangam-Signature"])
    assert json.loads(req.content)["type"] == "payment_intent.succeeded"


def test_stripe_inbound_webhook_signature(client, merchant):
    h = merchant["h"]
    acct = client.put("/v1/providers/stripe", json={"credentials": {"secret_key": "sk_test_x", "webhook_secret": "whsec_abc"}},
                      headers=h).json()
    url = acct["webhook_url"].replace("http://testserver", "")
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {"id": "cs_unknown", "payment_status": "paid"}}}).encode()
    ts = int(time.time())
    good = hmac.new(b"whsec_abc", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={"Stripe-Signature": f"t={ts},v1={good}"}).json() == {"received": True, "handled": False}
    assert client.post(url, content=body, headers={"Stripe-Signature": f"t={ts},v1=deadbeef"}).status_code == 400


def test_sign_verify_roundtrip():
    body = b'{"x":1}'
    assert verify("s", body, sign("s", body))
    assert not verify("s", body, sign("s", body, timestamp=int(time.time()) - 3600))
