# SangamPay for WooCommerce

Adds a **SangamPay** payment method to WooCommerce: UPI (including zero-fee direct UPI), net banking, cards,
bank debits and PayPal, all through your own SangamPay server.

## Install

1. Zip the `sangampay-woocommerce` folder and upload it in **WordPress → Plugins → Add New → Upload**, then activate.
2. In SangamPay → **Developers**, create a key with the **developer** role.
3. In **WooCommerce → Settings → Payments → SangamPay**, enter your server URL and that key.
4. In SangamPay → **Developers → Webhook endpoints**, add `https://yourshop.com/?wc-api=sangampay`
   (the exact URL is shown on the settings page) and paste the `whsec_…` secret into the plugin settings.

## What it does

- Creates a SangamPay payment for each order (idempotent per order + total) and redirects to hosted checkout.
- Marks orders paid only from **signed webhooks**, never from the browser redirect.
- Puts orders on hold while a bank debit clears or a direct-UPI UTR awaits verification.
- Supports full and partial refunds from the WooCommerce order screen.
- Compatible with WooCommerce High-Performance Order Storage (HPOS).
