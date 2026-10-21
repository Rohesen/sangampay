# sangampay (Node.js)

Zero-dependency client for a [SangamPay](../../README.md) server. Node 18+, TypeScript types included.

```bash
npm install ./sdks/node
```

```js
const { SangamPay, verifyWebhook } = require("sangampay");
const sp = new SangamPay(process.env.SANGAM_SECRET_KEY, { baseUrl: "http://localhost:8000" });

const pi = await sp.paymentIntents.create(
  { amount: 49900, currency: "INR", description: "Order #1001", customer: { email: "asha@example.in" } },
  { idempotencyKey: "order-1001" },
);
res.redirect(pi.checkout_url);

// Express: keep the raw body for signature checks
app.post("/webhooks/sangam", express.raw({ type: "application/json" }), (req, res) => {
  const event = verifyWebhook(req.body, req.get("Sangam-Signature"), process.env.SANGAM_WEBHOOK_SECRET);
  if (event.type === "payment_intent.succeeded") fulfil(event.data.id);
  res.sendStatus(200);
});
```
