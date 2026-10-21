# sangampay (Python)

Zero-dependency client for a [SangamPay](../../README.md) server.

```bash
pip install ./sdks/python
```

```python
from sangampay import SangamPay, verify_webhook

sp = SangamPay("sk_test_...", base_url="http://localhost:8000")

pi = sp.payment_intents.create(amount=49900, currency="INR", description="Order #1001",
                               customer={"email": "asha@example.in"}, idempotency_key="order-1001")
print(pi["checkout_url"])          # send the customer here

cus = sp.customers.create(name="Kiran Traders", gstin="29ABCDE1234F1Z5")
plan = sp.plans.create(name="Pro", amount=49900, currency="INR", interval="month", tax_rate=18)
sub = sp.subscriptions.create(customer=cus["id"], plan=plan["id"], collection="mandate", mandate_method="upi")
print(sub["mandate"]["authorization_url"])  # customer approves UPI AutoPay once

# Flask / FastAPI / Django webhook handler
event = verify_webhook(request_body_bytes, headers["Sangam-Signature"], "whsec_...")
if event["type"] == "payment_intent.succeeded":
    fulfil(event["data"]["id"])
```

Errors raise `SangamPayError` with `.status`, `.code`, `.message` and `.param`. GET requests, and POSTs sent
with an `idempotency_key`, are retried automatically on network errors, 429 and 5xx.
