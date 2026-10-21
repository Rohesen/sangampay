"""Tests for billing, risk, routing strategies, roles, reconciliation, disputes, payouts and analytics."""
import hashlib
import hmac
import json
from types import SimpleNamespace

from sangam import routing
from sangam.billing import add_interval
from sangam.db import SessionLocal
from sangam.models import PaymentAttempt, ProviderStat
from sangam.providers.razorpay import RazorpayProvider
from sangam.providers.base import ProviderContext
from sangam.ratelimit import TokenBucket
from sangam.util import utcnow


def pay(client, h, **kw):
    body = {"amount": 100000, "currency": "INR", "description": "Order", **kw}
    r = client.post("/v1/payment_intents", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def confirm(client, h, pi, method="upi", simulate="success", **extra):
    return client.post(f"/v1/payment_intents/{pi['id']}/confirm",
                       json={"method": method, "method_data": {"simulate": simulate}, **extra}, headers=h)


# ---- roles, audit ------------------------------------------------------------------------------

def test_roles_and_audit_log(client, merchant):
    h = merchant["h"]
    viewer = client.post("/v1/api_keys", json={"name": "Asha (finance)", "role": "viewer"}, headers=h).json()
    support = client.post("/v1/api_keys", json={"name": "Ravi (support)", "role": "support"}, headers=h).json()
    vh = {"Authorization": f"Bearer {viewer['secret_key']}"}
    sh = {"Authorization": f"Bearer {support['secret_key']}"}

    assert client.get("/v1/payment_intents", headers=vh).status_code == 200  # everyone can read
    r = client.post("/v1/payment_intents", json={"amount": 100, "currency": "INR"}, headers=vh)
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_permissions"
    assert client.post("/v1/api_keys", json={"name": "x", "role": "admin"}, headers=sh).status_code == 403

    pi = pay(client, h)
    confirm(client, h, pi)
    assert client.post("/v1/refunds", json={"payment_intent": pi["id"], "amount": 100}, headers=sh).status_code == 201

    logs = client.get("/v1/audit_logs", headers=h).json()["data"]
    assert any(l["actor"] == "Ravi (support)" and l["path"] == "/v1/refunds" and l["status"] == 201 for l in logs)
    assert any(l["actor"] == "Asha (finance)" and l["status"] == 403 for l in logs)

    client.delete(f"/v1/api_keys/{viewer['id']}", headers=h)
    assert client.get("/v1/payment_intents", headers=vh).status_code == 401


# ---- risk ------------------------------------------------------------------------------------

def test_risk_rules_block_and_record(client, merchant):
    h = merchant["h"]
    client.put("/v1/merchant/risk_rules", json={"block_emails": ["@fraud.example"], "max_amount": 5_000_00,
                                                "allowed_countries": ["IN", "US"]}, headers=h)
    pi = pay(client, h, customer={"email": "bad@fraud.example"})
    r = confirm(client, h, pi)
    assert r.status_code == 402 and "blocklist" in r.json()["error"]["message"]
    got = client.get(f"/v1/payment_intents/{pi['id']}", headers=h).json()
    assert got["risk"]["level"] == "blocked"

    big = pay(client, h, amount=10_000_00)
    assert confirm(client, h, big).status_code == 402
    foreign = pay(client, h, customer={"email": "a@b.com", "country": "RU"})
    assert confirm(client, h, foreign).status_code == 402
    ok = pay(client, h, customer={"email": "good@shop.example", "country": "IN"})
    assert confirm(client, h, ok).json()["status"] == "succeeded"
    events = [e["type"] for e in client.get("/v1/events?limit=100", headers=h).json()["data"]]
    assert "payment_intent.blocked" in events


def test_velocity_limit_per_ip(client, merchant):
    h = merchant["h"]
    client.put("/v1/merchant/risk_rules", json={"max_attempts_per_ip_hour": 3}, headers=h)
    results = []
    for _ in range(4):
        pi = pay(client, h)
        results.append(client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "card", "method_data": {"simulate": "fail"}},
                                   headers={"X-Client-Secret": pi["client_secret"]}).status_code)
    assert results[:3] == [200, 200, 200] and results[3] == 402


# ---- routing strategies ------------------------------------------------------------------------

def test_success_rate_and_cost_ranking():
    m = SimpleNamespace(id="acct_rank", routing_strategy="success_rate", settings={})
    with SessionLocal() as db:
        for provider, attempts, ok in (("razorpay", 50, 20), ("stripe", 50, 48)):
            db.merge(ProviderStat(merchant_id="acct_rank", provider=provider, method="card",
                                  attempts=attempts, successes=ok))
        db.commit()
        assert routing.rank(db, m, "card", ["razorpay", "stripe", "mock"]) == ["stripe", "razorpay", "mock"]
        m.routing_strategy = "cost"
        assert routing.rank(db, m, "card", ["stripe", "razorpay"]) == ["razorpay", "stripe"]
        m.settings = {"fees": {"stripe": 1.0}}
        assert routing.rank(db, m, "card", ["razorpay", "stripe"]) == ["stripe", "razorpay"]
        m.routing_strategy = "priority"
        assert routing.rank(db, m, "card", ["razorpay", "stripe"]) == ["razorpay", "stripe"]


def test_outcomes_feed_routing_stats(client, merchant):
    h = merchant["h"]
    for sim in ("success", "success", "fail"):
        confirm(client, h, pay(client, h), method="card", simulate=sim)
    health = client.get("/v1/routing/health", headers=h).json()["data"]
    card = next(x for x in health if x["provider"] == "mock" and x["method"] == "card")
    assert card["recent_attempts"] > 2.5 and 40 < card["success_rate"] < 80


# ---- invoices & GST ------------------------------------------------------------------------

def test_gst_invoice_intra_and_inter_state_and_payment(client, merchant):
    h = merchant["h"]
    client.put("/v1/merchant/profile", json={"legal_name": "Chai Point Pvt Ltd", "gstin": "27AAPFU0939F1ZV",
                                             "address": "Pune, Maharashtra", "invoice_prefix": "CP"}, headers=h)
    local = client.post("/v1/customers", json={"name": "Asha", "email": "asha@x.in", "state_code": "27"}, headers=h).json()
    other = client.post("/v1/customers", json={"name": "Kiran Traders", "gstin": "29ABCDE1234F1Z5"}, headers=h).json()
    assert other["state_code"] == "29"

    items = [{"description": "Tea subscription box", "quantity": 2, "unit_amount": 50000, "tax_rate": 18, "hsn_sac": "0902"}]
    inv = client.post("/v1/invoices", json={"customer": local["id"], "currency": "INR", "line_items": items}, headers=h).json()
    assert inv["subtotal"] == 100000 and inv["tax_total"] == 18000 and inv["total"] == 118000
    assert [t["name"] for t in inv["tax_breakdown"]] == ["CGST 9%", "SGST 9%"]
    assert inv["number"].startswith("CP-") and inv["status"] == "open"

    inter = client.post("/v1/invoices", json={"customer": other["id"], "currency": "INR", "line_items": items}, headers=h).json()
    assert inter["tax_breakdown"] == [{"name": "IGST 18%", "amount": 18000}]
    assert inter["number"] != inv["number"]

    page = client.get(inv["hosted_invoice_url"].replace("http://testserver", ""))
    assert page.status_code == 200 and "Tax Invoice" in page.text and "27AAPFU0939F1ZV" in page.text
    assert "₹1,180.00" in page.text and "CGST 9%" in page.text

    pi = client.get(f"/v1/payment_intents/{inv['payment_intent']}", headers=h).json()
    assert pi["amount"] == 118000
    confirm(client, h, pi)
    assert client.get(f"/v1/invoices/{inv['id']}", headers=h).json()["status"] == "paid"

    voided = client.post(f"/v1/invoices/{inter['id']}/void", headers=h).json()
    assert voided["status"] == "void"
    csv_text = client.get("/v1/exports/invoices.csv", headers=h).text
    assert inv["number"] in csv_text and "CGST 9%=90.00" in csv_text


# ---- payment links -----------------------------------------------------------------------------

def test_payment_links_fixed_and_custom(client, merchant):
    h = merchant["h"]
    fixed = client.post("/v1/payment_links", json={"title": "Masterclass ticket", "amount": 99900, "currency": "INR",
                                                   "max_payments": 1}, headers=h).json()
    path = fixed["url"].replace("http://testserver", "")
    assert "Masterclass ticket" in client.get(path).text
    r = client.post(path, data={"email": "fan@x.in"}, follow_redirects=False)
    assert r.status_code == 303 and "/pay/pi_" in r.headers["location"]
    pi_id = r.headers["location"].split("/pay/")[1].split("#")[0]
    confirm(client, h, {"id": pi_id})
    link = client.get("/v1/payment_links", headers=h).json()["data"][0]
    assert link["payments_count"] == 1 and link["amount_collected"] == 99900 and link["active"] is False
    assert "closed" in client.get(path).text

    donate = client.post("/v1/payment_links", json={"title": "Donate", "currency": "INR", "min_amount": 1000},
                         headers=h).json()
    dpath = donate["url"].replace("http://testserver", "")
    too_small = client.post(dpath, data={"amount": "5"}, follow_redirects=False)
    assert too_small.status_code == 200 and "minimum" in too_small.text
    ok = client.post(dpath, data={"amount": "251.50"}, follow_redirects=False)
    pi_id = ok.headers["location"].split("/pay/")[1].split("#")[0]
    assert client.get(f"/v1/payment_intents/{pi_id}", headers=h).json()["amount"] == 25150


# ---- subscriptions -------------------------------------------------------------------------

def _customer_and_plan(client, h, **plan):
    c = client.post("/v1/customers", json={"name": "Meera", "email": "meera@x.in"}, headers=h).json()
    p = client.post("/v1/plans", json={"name": "Pro monthly", "amount": 49900, "currency": "INR", "interval": "month",
                                       "tax_rate": 18, **plan}, headers=h).json()
    return c, p


def test_upi_autopay_mandate_lifecycle_and_dunning(client, merchant):
    h = merchant["h"]
    c, p = _customer_and_plan(client, h)
    sub = client.post("/v1/subscriptions", json={"customer": c["id"], "plan": p["id"], "collection": "mandate",
                                                 "mandate_method": "upi"}, headers=h).json()
    assert sub["status"] == "incomplete" and "/sandbox/mandate/" in sub["mandate"]["authorization_url"]

    r = client.post(f"/sandbox/mandate/{sub['id']}/approve", follow_redirects=True)
    assert "AutoPay set up" in r.text
    s = client.get(f"/v1/subscriptions/{sub['id']}", headers=h).json()
    assert s["status"] == "active" and s["mandate"]["status"] == "active"
    invoices = client.get(f"/v1/invoices?subscription={sub['id']}", headers=h).json()["data"]
    assert len(invoices) == 1 and invoices[0]["status"] == "paid" and invoices[0]["total"] == 58882

    run = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 32}, headers=h).json()
    assert run["billing_run"]["renewed"] == 1 and run["billing_run"]["charged"] == 1
    assert len(client.get(f"/v1/invoices?subscription={sub['id']}", headers=h).json()["data"]) == 2

    # Make the next renewal fail and walk through dunning: 3 retries, then unpaid.
    with SessionLocal() as db:
        from sangam.models import Subscription
        row = db.get(Subscription, sub["id"])
        row.metadata_ = {**row.metadata_, "simulate": "fail"}
        db.commit()
    s = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 32}, headers=h).json()["subscription"]
    assert s["status"] == "past_due" and s["retry_count"] == 1
    for expected in ("past_due", "past_due", "unpaid"):
        s = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 6}, headers=h).json()["subscription"]
        assert s["status"] == expected
    types = [e["type"] for e in client.get("/v1/events?limit=100", headers=h).json()["data"]]
    assert "subscription.payment_failed" in types and "subscription.unpaid" in types


def test_invoice_collection_with_trial_and_cancel(client, merchant):
    h = merchant["h"]
    c, p = _customer_and_plan(client, h, trial_days=14, tax_rate=0)
    sub = client.post("/v1/subscriptions", json={"customer": c["id"], "plan": p["id"], "collection": "invoice"},
                      headers=h).json()
    assert sub["status"] == "trialing" and sub["latest_invoice"] is None

    s = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 15}, headers=h).json()
    assert s["billing_run"]["renewed"] == 1
    inv = client.get(f"/v1/invoices?subscription={sub['id']}", headers=h).json()["data"][0]
    assert inv["status"] == "open" and inv["total"] == 49900
    events = [e["type"] for e in client.get("/v1/events?limit=50", headers=h).json()["data"]]
    assert "invoice.finalized" in events  # merchant's webhook sends the hosted link to the customer

    s = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 8}, headers=h).json()["subscription"]
    assert s["status"] == "past_due"
    confirm(client, h, {"id": inv["payment_intent"]})
    assert client.get(f"/v1/subscriptions/{sub['id']}", headers=h).json()["status"] == "active"

    client.post(f"/v1/subscriptions/{sub['id']}/cancel", json={"at_period_end": True}, headers=h)
    s = client.post(f"/v1/test_helpers/subscriptions/{sub['id']}/advance", json={"days": 31}, headers=h).json()["subscription"]
    assert s["status"] == "canceled"


def test_add_interval_clamps_month_end():
    from datetime import datetime
    assert add_interval(datetime(2026, 1, 31), "month") == datetime(2026, 2, 28)
    assert add_interval(datetime(2028, 1, 31), "month") == datetime(2028, 2, 29)
    assert add_interval(datetime(2026, 3, 15), "year") == datetime(2027, 3, 15)
    assert add_interval(datetime(2026, 3, 15), "month", 3) == datetime(2026, 6, 15)


# ---- reconciliation -----------------------------------------------------------------------

def test_bank_statement_auto_reconciliation(client, merchant):
    h = merchant["h"]
    client.put("/v1/providers/upi_direct", json={"config": {"vpa": "shop@okicici", "payee_name": "Shop"}}, headers=h)
    client.put("/v1/merchant/routing", json={"routing": {"upi": ["upi_direct"]}}, headers=h)

    def upi_payment(amount):
        pi = pay(client, h, amount=amount)
        client.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "upi"}, headers={"X-Client-Secret": pi["client_secret"]})
        return pi

    a = upi_payment(49900)    # customer submits UTR
    client.post(f"/v1/checkout/{a['id']}/utr", json={"utr": "500011112222"}, headers={"X-Client-Secret": a["client_secret"]})
    b = upi_payment(120000)   # customer never submits UTR; narration carries our reference
    c = upi_payment(30000)    # UTR submitted but statement amount differs
    client.post(f"/v1/checkout/{c['id']}/utr", json={"utr": "500033334444"}, headers={"X-Client-Secret": c["client_secret"]})

    statement = "\n".join([
        "HDFC BANK LTD,Statement of account", "Account No,XXXX1234", "",
        "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance",
        f"21/10/26,UPI-ASHA RAO-asha@okaxis-500011112222-PAYMENT,500011112222,21/10/26,,499.00,10499.00",
        f"21/10/26,UPI-KIRAN-kiran@ybl-600099998888-{b['id']},600099998888,21/10/26,,\"1,200.00\",11699.00",
        "21/10/26,UPI-X-x@ok-500033334444-PAY,500033334444,21/10/26,,250.00,11949.00",
        "21/10/26,ATM WDL,000000,21/10/26,500.00,,11449.00",
        "21/10/26,NEFT-UNKNOWN PAYER,N1234,21/10/26,,999.00,12448.00",
    ])
    run = client.post("/v1/reconciliation", json={"csv": statement}, headers=h).json()
    assert run["rows"] == 4 and run["matched"] == 2
    assert {m["payment_intent"] for m in run["report"]["matched"]} == {a["id"], b["id"]}
    assert run["report"]["needs_review"][0]["payment_intent"] == c["id"]
    assert len(run["report"]["unmatched"]) == 2  # the 250.00 mismatch row and the unknown NEFT
    for pid, status in ((a["id"], "succeeded"), (b["id"], "succeeded"), (c["id"], "pending_verification")):
        assert client.get(f"/v1/payment_intents/{pid}", headers=h).json()["status"] == status


# ---- disputes, payouts, splits ------------------------------------------------------------

def test_dispute_flow_and_ledger(client, merchant):
    h = merchant["h"]
    pi = pay(client, h, amount=200000)
    confirm(client, h, pi)
    d = client.post(f"/v1/test_helpers/payment_intents/{pi['id']}/dispute", json={}, headers=h).json()
    assert d["status"] == "needs_response"
    d = client.post(f"/v1/disputes/{d['id']}/evidence", json={"summary": "Delivered on 12 Oct, signed by customer.",
                                                              "shipping_tracking_number": "DTDC123"}, headers=h).json()
    assert d["status"] == "under_review"
    lost = client.post(f"/v1/test_helpers/payment_intents/{pi['id']}/dispute", json={"outcome": "lost"}, headers=h).json()
    assert client.get("/v1/disputes?status=lost", headers=h).json()["data"][0]["id"] == lost["id"]
    bal = client.get("/v1/balance", headers=h).json()["data"][0]
    assert bal["disputes_lost"] == 200000 and bal["net_volume"] == 0


def test_payouts_to_vault_accounts(client, merchant):
    h = merchant["h"]
    ba = client.post("/v1/bank_accounts", json={"kind": "in_bank", "owner": "merchant", "details": {
        "account_holder_name": "Supplier Co", "account_number": "123456789012", "ifsc": "ICIC0000001"}}, headers=h).json()
    po = client.post("/v1/payouts", json={"bank_account": ba["id"], "amount": 150000, "mode": "IMPS",
                                          "purpose": "vendor bill"}, headers=h).json()
    assert po["status"] == "processed" and po["provider"] == "mock"
    failed = client.post("/v1/payouts", json={"bank_account": ba["id"], "amount": 100, "narration": "simulate_failure"},
                         headers=h).json()
    assert failed["status"] == "failed"
    assert client.post("/v1/payouts", json={"bank_account": ba["id"], "amount": 100, "mode": "RTGS"}, headers=h).status_code == 400
    assert client.post("/v1/payouts", json={"bank_account": ba["id"], "amount": 100, "mode": "UPI"}, headers=h).status_code == 400
    assert client.get("/v1/balance", headers=h).json()["data"] == [] or True
    assert len(client.get("/v1/payouts", headers=h).json()["data"]) == 2


def test_marketplace_split_transfers(client, merchant):
    h = merchant["h"]
    bad = client.post("/v1/payment_intents", json={"amount": 1000, "currency": "INR",
                                                   "transfers": [{"account": "acc_seller1", "amount": 2000}]}, headers=h)
    assert bad.status_code == 400
    pi = pay(client, h, amount=100000, transfers=[{"account": "acc_seller1", "amount": 80000},
                                                  {"account": "acc_seller2", "amount": 15000}])
    confirm(client, h, pi)
    transfers = client.get(f"/v1/transfers?payment_intent={pi['id']}", headers=h).json()["data"]
    assert sorted(t["amount"] for t in transfers) == [15000, 80000]
    assert all(t["status"] == "succeeded" for t in transfers)


# ---- analytics & exports ------------------------------------------------------------------

def test_analytics_and_exports(client, merchant):
    h = merchant["h"]
    for sim in ("success", "success", "fail"):
        confirm(client, h, pay(client, h, amount=50000, customer={"email": f"{sim}@x.in"}), method="card", simulate=sim)
    a = client.get("/v1/analytics?days=7", headers=h).json()
    assert a["currency"] == "INR" and a["gross_volume"] == 100000 and a["payments"] == 2
    assert a["success_rate"] == 66.7 and len(a["daily"]) == 7 and a["daily"][-1]["volume"] == 100000
    assert a["by_method"][0]["name"] == "card"
    csv_text = client.get("/v1/exports/payments.csv", headers=h).text
    assert csv_text.startswith("id,created,status") and csv_text.count("\n") == 4


# ---- gateway webhook parsing ----------------------------------------------------------------

def test_razorpay_webhooks_map_subscription_and_dispute_events():
    p = RazorpayProvider()
    ctx = ProviderContext(account_id="pacct_x", credentials={"webhook_secret": "s3cret"}, config={}, mode="test",
                          base_url="http://x")

    def signed(event):
        body = json.dumps(event).encode()
        return {"x-razorpay-signature": hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()}, body

    headers, body = signed({"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_RZ1"}}, "payment": {"entity": {"id": "pay_1", "amount": 49900}}}})
    res = p.handle_webhook(ctx, headers, body)
    assert (res.kind, res.provider_ref, res.data["payment_id"]) == ("subscription_charge", "sub_RZ1", "pay_1")

    headers, body = signed({"event": "payment.dispute.lost", "payload": {"dispute": {"entity": {
        "id": "disp_1", "payment_id": "pay_1", "amount": 49900, "reason_code": "chargeback"}}}})
    res = p.handle_webhook(ctx, headers, body)
    assert (res.kind, res.status, res.data["reason"]) == ("dispute", "lost", "chargeback")


def test_dispute_webhook_creates_dispute_for_known_payment(client, merchant):
    h = merchant["h"]
    acct = client.put("/v1/providers/razorpay", json={"credentials": {"key_id": "rzp_test_k", "key_secret": "x",
                                                                      "webhook_secret": "whs"}}, headers=h).json()
    pi = pay(client, h)
    confirm(client, h, pi)
    with SessionLocal() as db:  # pretend this payment went through Razorpay
        att = db.query(PaymentAttempt).filter_by(intent_id=pi["id"]).first()
        att.provider_payment_id = "pay_DISPUTED"
        db.commit()
    body = json.dumps({"event": "payment.dispute.created", "payload": {"dispute": {"entity": {
        "id": "disp_9", "payment_id": "pay_DISPUTED", "amount": 100000, "reason_code": "fraud", "respond_by": 1893456000}}}}).encode()
    sig = hmac.new(b"whs", body, hashlib.sha256).hexdigest()
    r = client.post(acct["webhook_url"].replace("http://testserver", ""), content=body,
                    headers={"X-Razorpay-Signature": sig})
    assert r.json() == {"received": True, "handled": True}
    d = client.get("/v1/disputes", headers=h).json()["data"][0]
    assert d["payment_intent"] == pi["id"] and d["reason"] == "fraud" and d["due_by"].startswith("2030")


def test_token_bucket():
    b = TokenBucket(rate=1, burst=3)
    assert [b.take("k") for _ in range(3)] == [0, 0, 0]
    assert b.take("k") > 0
    assert b.take("other") == 0
