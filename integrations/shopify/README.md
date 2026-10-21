# SangamPay ⇄ Shopify bridge

Shopify only allows approved partners to publish payment-gateway apps, so a third-party gateway can't appear inside
Shopify's own checkout. This bridge uses Shopify's supported **manual payment method** flow instead, and the customer
still pays on SangamPay's checkout page.

```
Customer checks out ──► Shopify order (manual method "UPI / Cards / Net banking (SangamPay)")
        │                     │ orders/create webhook
        │                     ▼
        │               bridge.py ──► SangamPay payment ──► pay link written onto the order
        ▼
 pays via link ──► SangamPay ──► payment_intent.succeeded webhook ──► bridge marks the order paid
```

## Setup

1. **Shopify admin → Settings → Payments → Manual payment methods → Create custom payment method**, named
   e.g. `UPI / Cards / Net banking (SangamPay)`.
2. **Settings → Apps → Develop apps**: create an app with `read_orders` and `write_orders` scopes, and copy the
   Admin API access token.
3. **Settings → Notifications → Webhooks**: add `Order creation` → `https://bridge.example.com/shopify/orders-create`
   (JSON). Copy the signing secret shown there.
4. In **SangamPay → Developers**, add a webhook endpoint `https://bridge.example.com/sangam/webhook` for
   `payment_intent.succeeded`, and create a **developer** API key.
5. Run the bridge (Python 3.10+, no dependencies):

```bash
export SHOPIFY_SHOP=your-store.myshopify.com SHOPIFY_ADMIN_TOKEN=shpat_... SHOPIFY_WEBHOOK_SECRET=...
export SHOPIFY_PAYMENT_METHOD="UPI / Cards / Net banking (SangamPay)"
export SANGAM_URL=https://pay.example.com SANGAM_SECRET_KEY=sk_live_... SANGAM_WEBHOOK_SECRET=whsec_...
python bridge.py --port 8787
```

6. Show the link to customers: in **Settings → Notifications → Order confirmation**, add

```liquid
{% for attribute in attributes %}{% if attribute.first == "Pay now" %}
  <p><a href="{{ attribute.last }}">Pay now with UPI, cards or net banking</a></p>
{% endif %}{% endfor %}
```

Both webhooks are signature-verified, payments are created idempotently per order, and failures return HTTP 500 so
Shopify and SangamPay retry delivery.
