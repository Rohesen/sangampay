/*! SangamPay browser SDK. Load from your Sangam server: <script src="https://pay.example.com/sdk/sangam.js"></script>
 *
 *   // 1. Your server creates a payment:  POST /v1/payment_intents  -> { id, checkout_url, ... }
 *   // 2. In the browser:
 *   Sangam.checkout({ checkoutUrl: pi.checkout_url });            // full-page redirect (best on mobile; UPI apps)
 *   Sangam.checkout({ checkoutUrl: pi.checkout_url, popup: true,  // or a popup window
 *                     onClose: () => refreshOrderStatus() });
 *
 * Always confirm the final status on your server (webhook or GET /v1/payment_intents/:id),
 * never by trusting the browser.
 */
(function (global) {
  "use strict";

  function checkout(opts) {
    if (!opts || !opts.checkoutUrl) throw new Error("Sangam.checkout: checkoutUrl is required");
    if (!opts.popup) { global.location.assign(opts.checkoutUrl); return null; }

    var w = 460, h = 720;
    var left = global.screenX + Math.max(0, (global.outerWidth - w) / 2);
    var top = global.screenY + Math.max(0, (global.outerHeight - h) / 2);
    var win = global.open(opts.checkoutUrl, "sangam_checkout",
      "width=" + w + ",height=" + h + ",left=" + left + ",top=" + top);
    if (!win) { global.location.assign(opts.checkoutUrl); return null; } // popup blocked
    var timer = setInterval(function () {
      if (win.closed) { clearInterval(timer); if (typeof opts.onClose === "function") opts.onClose(); }
    }, 500);
    return win;
  }

  global.Sangam = { checkout: checkout, version: "1.0.0" };
})(window);
