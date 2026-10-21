"use strict";
/**
 * SangamPay Node.js SDK. Zero dependencies, Node 18+ (uses global fetch).
 *
 *   const { SangamPay, verifyWebhook } = require("sangampay");
 *   const sp = new SangamPay("sk_test_...", { baseUrl: "https://pay.example.com" });
 *   const pi = await sp.paymentIntents.create({ amount: 49900, currency: "INR" }, { idempotencyKey: "order-1001" });
 */
const crypto = require("crypto");

const VERSION = "1.0.0";

class SangamPayError extends Error {
  constructor(status, code, message, param) {
    super(`[${status} ${code}] ${message}`);
    Object.assign(this, { status, code, param, name: "SangamPayError" });
  }
}

/** Verify a Sangam-Signature header. `rawBody` must be the exact bytes received (Buffer or string). */
function verifyWebhook(rawBody, signatureHeader, secret, toleranceSeconds = 300) {
  const parts = Object.fromEntries(String(signatureHeader || "").split(",").map(kv => kv.split("=")));
  const ts = Number(parts.t);
  if (!ts || !parts.v1) throw new Error("Malformed Sangam-Signature header");
  if (Math.abs(Date.now() / 1000 - ts) > toleranceSeconds) throw new Error("Webhook timestamp outside tolerance");
  const body = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(rawBody);
  const expected = crypto.createHmac("sha256", secret).update(Buffer.concat([Buffer.from(`${ts}.`), body])).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(parts.v1);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) throw new Error("Webhook signature mismatch");
  return JSON.parse(body.toString("utf8"));
}

class Resource {
  constructor(client, path) { this._c = client; this._p = path; }
  create(params, opts = {}) { return this._c.request("POST", this._p, params, opts); }
  retrieve(id) { return this._c.request("GET", `${this._p}/${id}`); }
  list(query = {}) {
    const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v != null)).toString();
    return this._c.request("GET", this._p + (q ? `?${q}` : ""));
  }
  action(id, name, params = {}) { return this._c.request("POST", `${this._p}/${id}/${name}`, params); }
}

class SangamPay {
  constructor(secretKey, { baseUrl = "http://localhost:8000", timeoutMs = 30000, maxRetries = 2 } = {}) {
    if (!secretKey) throw new Error("SangamPay: secret key is required");
    Object.assign(this, { secretKey, baseUrl: baseUrl.replace(/\/$/, ""), timeoutMs, maxRetries });
    const r = p => new Resource(this, p);
    this.paymentIntents = Object.assign(r("/v1/payment_intents"), {
      confirm: (id, method, methodData = {}) => this.request("POST", `/v1/payment_intents/${id}/confirm`, { method, method_data: methodData }),
      cancel: id => this.request("POST", `/v1/payment_intents/${id}/cancel`, {}),
      verify: (id, approve = true, note) => this.request("POST", `/v1/payment_intents/${id}/verify`, { approve, note }),
    });
    this.refunds = r("/v1/refunds");
    this.customers = r("/v1/customers");
    this.plans = r("/v1/plans");
    this.subscriptions = Object.assign(r("/v1/subscriptions"), {
      cancel: (id, atPeriodEnd = false) => this.request("POST", `/v1/subscriptions/${id}/cancel`, { at_period_end: atPeriodEnd }),
    });
    this.invoices = Object.assign(r("/v1/invoices"), {
      void: id => this.request("POST", `/v1/invoices/${id}/void`, {}),
      markPaid: (id, note) => this.request("POST", `/v1/invoices/${id}/mark_paid`, { note }),
    });
    this.paymentLinks = r("/v1/payment_links");
    this.payouts = r("/v1/payouts");
    this.bankAccounts = r("/v1/bank_accounts");
    this.disputes = r("/v1/disputes");
    this.webhookEndpoints = r("/v1/webhook_endpoints");
    this.events = r("/v1/events");
  }

  reconcile(csv, autoApprove = true) { return this.request("POST", "/v1/reconciliation", { csv, auto_approve: autoApprove }); }
  analytics(days = 30) { return this.request("GET", `/v1/analytics?days=${days}`); }
  balance() { return this.request("GET", "/v1/balance"); }

  async request(method, path, body, { idempotencyKey } = {}) {
    const headers = { Authorization: `Bearer ${this.secretKey}`, "Content-Type": "application/json", "User-Agent": `sangampay-node/${VERSION}` };
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const safeToRetry = method === "GET" || Boolean(idempotencyKey);
    for (let attempt = 0; ; attempt++) {
      let res;
      try {
        res = await fetch(this.baseUrl + path, { method, headers, body: body == null ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(this.timeoutMs) });
      } catch (err) {
        if (safeToRetry && attempt < this.maxRetries) { await sleep(2 ** attempt * 1000); continue; }
        throw err;
      }
      const data = await res.json().catch(() => ({}));
      if (res.ok) return data;
      if (safeToRetry && attempt < this.maxRetries && (res.status === 429 || res.status >= 500)) {
        await sleep(Number(res.headers.get("retry-after") || 2 ** attempt) * 1000); continue;
      }
      const e = data.error || {};
      throw new SangamPayError(res.status, e.code || "error", e.message || res.statusText, e.param);
    }
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

module.exports = { SangamPay, SangamPayError, verifyWebhook, VERSION };
