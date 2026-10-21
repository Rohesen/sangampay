"""Fill a running SangamPay server with realistic test-mode demo data.

    python -m sangam.demo                       # http://localhost:8000
    python -m sangam.demo --url https://pay.example.com

Prints a test secret key you can paste into the dashboard. Uses only the public API, so it is also a
worked example of most endpoints."""
from __future__ import annotations

import argparse
import random

import httpx

CUSTOMERS = [
    ("Asha Rao", "asha@example.in", "27", None), ("Kiran Traders", "accounts@kirantraders.in", None, "29ABCDE1234F1Z5"),
    ("Meera Iyer", "meera@example.in", "33", None), ("Rohan Gupta", "rohan@example.in", "07", None),
    ("Lotus Cafe", "billing@lotuscafe.in", None, "27AACCL1234M1Z2"), ("Dev Shah", "dev@example.in", "24", None),
]
PRODUCTS = [("Masala chai x10", 49900), ("Premium tea hamper", 249900), ("Green tea sampler", 89900),
            ("Assam gold 500g", 64900), ("Office chai subscription", 1499900), ("Gift card", 100000)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--name", default="Chai Point")
    ap.add_argument("--payments", type=int, default=34)
    ap.add_argument("--backdate-db", help="Server's SANGAM_DATABASE_URL, to spread payments over 30 days for nicer charts")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.url, timeout=30)
    rnd = random.Random(7)

    m = c.post("/v1/merchants", json={"name": args.name, "email": "owner@chaipoint.example", "country": "IN"})
    m.raise_for_status()
    key = m.json()["secret_key_test"]
    c.headers["Authorization"] = f"Bearer {key}"

    def call(method: str, path: str, **kw):
        r = c.request(method, path, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json()

    call("PUT", "/v1/merchant/profile", json={"legal_name": f"{args.name} Beverages Pvt Ltd", "gstin": "27AAPFU0939F1ZV",
                                              "address": "14 FC Road, Shivajinagar\nPune, Maharashtra 411005",
                                              "support_email": "help@chaipoint.example", "invoice_prefix": "CP"})
    call("PUT", "/v1/merchant/settings", json={"routing_strategy": "success_rate"})
    # Demo customers pay many times an hour; relax velocity rules so the sandbox doesn't flag them.
    call("PUT", "/v1/merchant/risk_rules", json={"max_attempts_per_email_hour": 1000, "max_attempts_per_ip_hour": 5000})

    customers = [call("POST", "/v1/customers", json={k: v for k, v in {"name": n, "email": e, "state_code": s,
                                                                         "gstin": g}.items() if v})
                 for n, e, s, g in CUSTOMERS]

    methods = ["upi", "upi", "upi", "card", "card", "netbanking", "bank_debit"]
    for i in range(args.payments):
        title, amount = rnd.choice(PRODUCTS)
        cust = rnd.choice(CUSTOMERS)
        method = rnd.choice(methods)
        currency = "INR" if method != "bank_debit" else "USD"
        pi = call("POST", "/v1/payment_intents", json={"amount": amount if currency == "INR" else amount // 80,
                                                       "currency": currency, "description": title,
                                                       "customer": {"email": cust[1], "name": cust[0], "country": "IN"}})
        outcome = "fail" if rnd.random() < 0.12 else "success"
        if i % 11 == 5:
            continue  # leave a few open
        res = call("POST", f"/v1/payment_intents/{pi['id']}/confirm", json={"method": method, "method_data": {"simulate": outcome}})
        if res["status"] == "succeeded" and i % 9 == 0:
            call("POST", "/v1/refunds", json={"payment_intent": pi["id"], "amount": pi["amount"] // 2})

    # Zero-fee UPI payments waiting for UTR verification (great for the reconciliation demo).
    call("PUT", "/v1/providers/upi_direct", json={"config": {"vpa": "chaipoint@okhdfcbank", "payee_name": args.name}})
    call("PUT", "/v1/merchant/routing", json={"routing": {"upi": ["upi_direct"]}})
    for n, utr in enumerate(("412345678901", "412345678902")):
        pi = call("POST", "/v1/payment_intents", json={"amount": [49900, 89900][n], "currency": "INR",
                                                       "description": "Counter order", "customer": {"email": "walkin@example.in"}})
        c.post(f"/v1/checkout/{pi['id']}/confirm", json={"method": "upi"}, headers={"X-Client-Secret": pi["client_secret"]})
        c.post(f"/v1/checkout/{pi['id']}/utr", json={"utr": utr}, headers={"X-Client-Secret": pi["client_secret"]})
    call("PUT", "/v1/merchant/routing", json={"routing": {"upi": ["razorpay", "upi_direct"]}})

    plan = call("POST", "/v1/plans", json={"name": "Office chai – monthly", "amount": 299900, "currency": "INR",
                                           "interval": "month", "tax_rate": 18, "hsn_sac": "996331"})
    call("POST", "/v1/plans", json={"name": "Tea club – yearly", "amount": 999900, "currency": "INR", "interval": "year",
                                    "trial_days": 14, "tax_rate": 5})
    for cust, method in zip(customers[:4], ("upi", "emandate", "card", "invoice")):
        sub = call("POST", "/v1/subscriptions", json={"customer": cust["id"], "plan": plan["id"],
                                                      "collection": "invoice" if method == "invoice" else "mandate",
                                                      "mandate_method": None if method == "invoice" else method})
        if method != "invoice":
            c.post(f"/sandbox/mandate/{sub['id']}/approve", follow_redirects=True)

    items = [{"description": "Corporate tea service – October", "quantity": 1, "unit_amount": 1850000, "tax_rate": 18,
              "hsn_sac": "996331"}, {"description": "Assam gold 500g", "quantity": 12, "unit_amount": 64900, "tax_rate": 5,
                                     "hsn_sac": "0902"}]
    inv = call("POST", "/v1/invoices", json={"customer": customers[1]["id"], "currency": "INR", "line_items": items,
                                             "notes": "Thank you for your business!"})
    call("POST", "/v1/invoices", json={"customer": customers[4]["id"], "currency": "INR", "line_items": items[:1]})

    call("POST", "/v1/payment_links", json={"title": "Chai tasting masterclass", "amount": 99900, "currency": "INR",
                                            "description": "Sat 2 Nov, 11am · Pune"})
    call("POST", "/v1/payment_links", json={"title": "Support local farmers", "currency": "INR", "min_amount": 10000})

    ba = call("POST", "/v1/bank_accounts", json={"kind": "in_bank", "owner": "merchant", "details": {
        "account_holder_name": "Assam Leaf Co-op", "account_number": "50100123456789", "ifsc": "HDFC0001234"}})
    call("POST", "/v1/payouts", json={"bank_account": ba["id"], "amount": 4500000, "mode": "NEFT", "purpose": "vendor bill",
                                      "narration": "Oct tea leaves"})
    vpa = call("POST", "/v1/bank_accounts", json={"kind": "upi_vpa", "owner": "merchant", "details": {
        "account_holder_name": "Ravi Kumar", "vpa": "ravi.kumar@okaxis"}})
    call("POST", "/v1/payouts", json={"bank_account": vpa["id"], "amount": 250000, "mode": "UPI", "purpose": "cashback"})

    paid = call("GET", "/v1/payment_intents?status=succeeded&limit=1")["data"]
    if paid:
        call("POST", f"/v1/test_helpers/payment_intents/{paid[0]['id']}/dispute", json={"reason": "product_not_received"})

    call("POST", "/v1/reconciliation", json={"rows": [
        {"amount": 49900, "text": "UPI-ASHA-asha@okaxis-412345678901-PAYMENT"},
        {"amount": 75000, "text": "NEFT-UNKNOWN PAYER"}]})

    if args.backdate_db:
        backdate(args.backdate_db, m.json()["merchant"]["id"], rnd)
    print(f"\nDemo data ready on {args.url}\nTest secret key: {key}\nInvoice: {inv['hosted_invoice_url']}\n")


def backdate(db_url: str, merchant_id: str, rnd: random.Random) -> None:
    """Spread one-off demo payments over the last 30 days so charts have history (writes the DB directly)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM payment_intents WHERE merchant_id = :m AND invoice_id IS NULL "
            "AND status NOT IN ('pending_verification', 'requires_action')"), {"m": merchant_id})]
        for pid in ids:
            ts = now - timedelta(days=rnd.randint(0, 29), hours=rnd.randint(0, 12), minutes=rnd.randint(0, 59))
            conn.execute(text("UPDATE payment_intents SET created_at = :t WHERE id = :i"), {"t": ts, "i": pid})
            conn.execute(text("UPDATE payment_attempts SET created_at = :t WHERE intent_id = :i"), {"t": ts, "i": pid})
    print(f"Backdated {len(ids)} payments across 30 days")


if __name__ == "__main__":
    main()
