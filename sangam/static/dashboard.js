/* SangamPay merchant dashboard. Plain JS, no build step. Talks only to the public /v1 API. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const ic = (d, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const I = {
    home: ic('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>'),
    pay: ic('<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M2 10h20"/>'),
    link: ic('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
    recon: ic('<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>'),
    dispute: ic('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'),
    invoice: ic('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>'),
    sub: ic('<path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>'),
    users: ic('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>'),
    payout: ic('<path d="M12 19V5M5 12l7-7 7 7"/><path d="M5 21h14"/>'),
    vault: ic('<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="12" cy="12" r="3"/><path d="M12 9V7M15 12h2"/>'),
    gw: ic('<path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18" cy="18" r="2"/>'),
    shield: ic('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'),
    code: ic('<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>'),
    gear: ic('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    plus: ic('<path d="M12 5v14M5 12h14"/>'),
    trend: ic('<path d="m3 17 6-6 4 4 8-8"/><path d="M14 7h7v7"/>', 16),
    undo: ic('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>', 16),
    check: ic('<path d="m5 12 4 4L19 6"/>', 16),
    receipt: ic('<path d="M4 2v20l3-2 3 2 3-2 3 2 3-2 3 2V2l-3 2-3-2-3 2-3-2-3 2z"/><path d="M8 9h8M8 13h6"/>', 16),
    bank: ic('<path d="M3 10 12 4l9 6"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8"/><path d="M3 21h18"/>'),
    lock: ic('<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'),
    trash: ic('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>', 15),
    upi: ic('<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/>', 15),
    card: ic('<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M2 10h20"/>', 15),
    nb: ic('<path d="M3 10 12 4l9 6"/><path d="M5 10v8M12 10v8M19 10v8"/>', 15),
    swap: ic('<path d="M7 7h12l-3-3M17 17H5l3 3"/>', 15),
    wallet: ic('<path d="M20 12V8H6a2 2 0 0 1 0-4h12v4"/><path d="M4 6v12a2 2 0 0 0 2 2h14v-4"/>', 15),
    ext: ic('<path d="M7 17 17 7M8 7h9v9"/>', 15),
    download: ic('<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>', 15),
    clock: ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', 15),
    qr: ic('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3M21 14v7h-7"/>', 15),
  };
  const methodIcon = {upi: I.upi, card: I.card, netbanking: I.nb, bank_debit: I.swap, paypal: I.wallet, emandate: I.bank};
  const methodName = {upi: "UPI", card: "Card", netbanking: "Net banking", bank_debit: "Bank debit", paypal: "PayPal", emandate: "e-NACH"};
  const STATUS = {
    succeeded: ["b-ok", "Succeeded"], processing: ["b-info", "Processing"], pending_verification: ["b-warn", "Verify UTR"],
    requires_action: ["b-neutral", "Awaiting customer"], requires_payment_method: ["b-neutral", "Open"],
    partially_refunded: ["b-info", "Part refunded"], refunded: ["b-neutral", "Refunded"], canceled: ["b-err", "Canceled"],
    // invoices / subscriptions / disputes / payouts
    open: ["b-warn", "Due"], paid: ["b-ok", "Paid"], void: ["b-neutral", "Void"], draft: ["b-neutral", "Draft"],
    active: ["b-ok", "Active"], trialing: ["b-info", "Trial"], past_due: ["b-warn", "Past due"], unpaid: ["b-err", "Unpaid"],
    incomplete: ["b-neutral", "Awaiting mandate"], needs_response: ["b-err", "Needs response"], under_review: ["b-info", "Under review"],
    won: ["b-ok", "Won"], lost: ["b-err", "Lost"], processed: ["b-ok", "Processed"], failed: ["b-err", "Failed"], reversed: ["b-warn", "Reversed"],
    pending: ["b-neutral", "Pending"], manual_action_required: ["b-warn", "Send manually"],
  };
  const badge = s => { const [c, l] = STATUS[s] || ["b-neutral", String(s).replaceAll("_", " ")]; return `<span class="badge ${c}">${l}</span>`; };
  const exp = c => ({JPY: 0, KRW: 0, VND: 0, CLP: 0, ISK: 0, UGX: 0, BHD: 3, KWD: 3, OMR: 3, JOD: 3, TND: 3}[c] ?? 2);
  const major = (a, c) => (a / 10 ** exp(c)).toLocaleString(c === "INR" ? "en-IN" : "en-US", {minimumFractionDigits: exp(c), maximumFractionDigits: exp(c)});
  const money = (a, c) => `<small>${esc(c)}</small>${major(a, c)}`;
  const toMinor = (v, c) => Math.round(parseFloat(v) * 10 ** exp(c));
  const when = d => { if (!d) return "—"; const t = new Date(d), s = (Date.now() - t) / 1000;
    if (s < 0) return t.toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
    if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)}m ago`; if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return t.toLocaleDateString(undefined, {day: "numeric", month: "short"}); };
  const date = d => d ? new Date(d).toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"}) : "—";

  let key = null; try { key = sessionStorage.getItem("sangam_key"); } catch {}
  let page = "overview", merchant = null, payFilter = "all";

  async function api(path, opts = {}) {
    const res = await fetch(path, {...opts, headers: {"Authorization": `Bearer ${key}`, "Content-Type": "application/json"},
      body: opts.body ? JSON.stringify(opts.body) : undefined});
    if (res.headers.get("content-type")?.includes("text/csv")) return res.text();
    const data = await res.json();
    if (!res.ok) throw new Error(data.error?.message || res.statusText);
    return data;
  }
  const post = (p, body) => api(p, {method: "POST", body: body || {}});
  const put = (p, body) => api(p, {method: "PUT", body});
  function toast(msg, err) {
    const t = document.createElement("div"); t.className = "toast" + (err ? " err" : "");
    t.innerHTML = (err ? "" : I.check) + `<span>${esc(msg)}</span>`;
    $("#toasts").append(t); setTimeout(() => t.remove(), 3800);
  }
  const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
  const bind = (sel, fn) => $$(sel).forEach(b => b.onclick = guard(() => fn(b.dataset, b)));

  function modal({title, sub = "", body = "", submit = "Save", danger = false, cancel = "Cancel", onOpen}) {
    const d = $("#modal");
    d.innerHTML = `<form method="dialog"><div class="m-head"><h3>${title}</h3>${sub ? `<p>${sub}</p>` : ""}</div>
      ${body ? `<div class="m-body">${body}</div>` : ""}
      <div class="m-foot">${cancel ? `<button class="btn ghost" value="cancel" formnovalidate>${cancel}</button>` : ""}<button class="btn ${danger ? "danger" : ""}" value="ok">${submit}</button></div></form>`;
    d.showModal();
    onOpen?.(d);
    d.querySelector("input:not([type=hidden]),select,textarea")?.focus();
    return new Promise(res => d.addEventListener("close", () => res(d.returnValue === "ok" ? new FormData(d.querySelector("form")) : null), {once: true}));
  }
  const field = (label, input, help = "") => `<div class="field"><label>${label}</label>${input}${help ? `<span class="help">${help}</span>` : ""}</div>`;
  const currencySelect = (name = "currency", sel = "INR") => `<select name="${name}">${["INR", "USD", "EUR", "GBP", "AED", "SGD"].map(c => `<option ${c === sel ? "selected" : ""}>${c}</option>`).join("")}</select>`;
  const empty = (icon, title, text) => `<div class="empty"><div class="big">${icon}</div><h3>${title}</h3><p>${text}</p></div>`;
  const head = (title, sub, actions = "") => `<div class="page-head"><div><h1>${title}</h1><p>${sub}</p></div><div class="row" style="display:flex;gap:8px;flex-wrap:wrap">${actions}</div></div>`;
  const table = (cols, rows) => `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th${c.startsWith(">") ? ' class="num"' : ""}>${c.replace(/^>/, "")}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  async function download(kind) {
    const text = await api(`/v1/exports/${kind}.csv`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], {type: "text/csv"}));
    a.download = `sangampay-${kind}.csv`; a.click(); URL.revokeObjectURL(a.href);
  }
  const copy = text => { navigator.clipboard?.writeText(text); toast("Copied"); };

  // ---------------------------------------------------------------- Login
  const LOGO = `<div class="mark">${ic('<path d="M4 18c4-8 12-8 16 0"/><path d="M4 12c4-6 12-6 16 0"/>', 18)}</div><b>Sangam<span>Pay</span></b>`;
  function loginView(msg = "") {
    $("#root").innerHTML = `<div class="login">
      <div class="login-art"><div class="logo">${LOGO}</div>
        <div><h2>Every payment rail, one open-source platform.</h2>
        <ul><li>${I.check} UPI, net banking, cards, bank debits &amp; PayPal</li><li>${I.check} Subscriptions with UPI AutoPay &amp; e-NACH</li>
        <li>${I.check} GST invoices, payment links, payouts</li><li>${I.check} Auto-reconciliation, fraud rules, smart routing</li>
        <li>${I.check} Money settles straight into your accounts</li></ul></div>
        <div style="position:relative;z-index:1;opacity:.7;font-size:12.5px">Open source · MIT licensed · self-hosted</div></div>
      <div class="login-form"><div class="box">
        <div><h1>Welcome back</h1><p>Sign in with your secret API key.</p></div>
        ${field("Secret key", `<input id="key" type="password" placeholder="sk_test_…" autocomplete="off">`, "Kept only in this browser tab, never stored.")}
        <button class="btn" id="go" style="justify-content:center;padding:12px">Sign in</button>
        <div class="divider">New to SangamPay?</div>
        <button class="btn ghost" id="signup" style="justify-content:center;padding:12px">Create a merchant account</button>
        ${msg ? `<p style="color:var(--err)">${esc(msg)}</p>` : ""}</div></div></div>`;
    const go = () => { key = $("#key").value.trim(); try { sessionStorage.setItem("sangam_key", key); } catch {} boot(); };
    $("#go").onclick = go; $("#key").onkeydown = e => e.key === "Enter" && go();
    $("#signup").onclick = guard(async () => {
      const f = await modal({title: "Create merchant account", sub: "You'll get a test key and a live key.", submit: "Create account",
        body: field("Business name", `<input name="name" required maxlength="120">`) + field("Business email", `<input name="email" type="email" required>`) +
          field("Country", `<select name="country"><option value="IN">India</option><option value="US">United States</option><option value="GB">United Kingdom</option><option value="DE">Germany</option><option value="SG">Singapore</option><option value="AE">UAE</option></select>`)});
      if (!f) return;
      const res = await fetch("/v1/merchants", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(Object.fromEntries(f))});
      const d = await res.json(); if (!res.ok) throw new Error(d.error?.message);
      await modal({title: "Save your API keys", sub: "Shown only once. SangamPay stores just their hashes.", submit: "I've saved them", cancel: "",
        body: `<div class="secret-box"><span class="cell-sub">Test secret key</span><code>${esc(d.secret_key_test)}</code></div>
               <div class="secret-box"><span class="cell-sub">Live secret key</span><code>${esc(d.secret_key_live)}</code></div>`});
      $("#key").value = d.secret_key_test;
    });
  }

  // ---------------------------------------------------------------- Shell
  const NAV = [
    [null, [["overview", "Overview", I.home]]],
    ["Payments", [["payments", "Payments", I.pay], ["links", "Payment links", I.link], ["recon", "Reconciliation", I.recon], ["disputes", "Disputes", I.dispute]]],
    ["Billing", [["invoices", "Invoices", I.invoice], ["subscriptions", "Subscriptions", I.sub], ["customers", "Customers", I.users]]],
    ["Money out", [["payouts", "Payouts & splits", I.payout], ["banks", "Bank vault", I.vault]]],
    ["Platform", [["gateways", "Gateways & routing", I.gw], ["risk", "Risk rules", I.shield], ["developers", "Developers", I.code], ["settings", "Settings", I.gear]]],
  ];
  function shell() {
    const live = merchant.mode === "live";
    $("#root").innerHTML = `<div class="app">
      <aside class="side"><div class="logo">${LOGO}</div>
        <nav class="nav">${NAV.map(([g, items]) => (g ? `<div class="nav-group">${g}</div>` : "") + items.map(([k, l, i]) => `<button data-page="${k}">${i}${l}</button>`).join("")).join("")}</nav>
        <div class="side-foot"><div class="mode ${live ? "live" : ""}"><i></i>${live ? "Live mode" : "Test mode"}</div>
          <div class="who"><div class="av">${esc(merchant.name[0].toUpperCase())}</div><div><div class="n">${esc(merchant.name)}</div>
          <div style="font-size:11.5px;opacity:.7">${esc(merchant.key.name)} · ${esc(merchant.key.role)}</div><button id="logout">Sign out</button></div></div></div></aside>
      <div><div class="mobile-bar"><div class="logo">${LOGO}</div><button class="btn sm ghost" id="logout2">Sign out</button></div>
        <div class="mobile-nav">${NAV.flatMap(([, items]) => items).map(([k, l]) => `<button data-page="${k}">${l}</button>`).join("")}</div>
        <main class="main" id="page"></main></div></div>`;
    $$("[data-page]").forEach(b => b.onclick = () => go(b.dataset.page));
    const out = () => { key = null; try { sessionStorage.removeItem("sangam_key"); } catch {} boot(); };
    $("#logout").onclick = out; $("#logout2").onclick = out;
  }
  const PAGES = {};
  function go(p) {
    page = p;
    $$("[data-page]").forEach(b => b.dataset.page === p ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
    $("#page").innerHTML = `<div class="card card-body" style="display:grid;gap:12px"><div class="sk" style="width:40%"></div><div class="sk"></div><div class="sk" style="width:70%"></div></div>`;
    PAGES[p]().catch(e => $("#page").innerHTML = `<div class="card">${empty(I.dispute, "Couldn't load this page", esc(e.message))}</div>`);
  }
  const reload = () => go(page);
  async function boot() {
    if (!key) return loginView();
    try { merchant = await api("/v1/merchant"); shell(); go(page); }
    catch (e) { key = null; loginView(e.message); }
  }

  // ---------------------------------------------------------------- Overview
  function barChart(daily, currency) {
    const W = 720, H = 220, P = {l: 52, r: 8, t: 12, b: 26};
    const max = Math.max(1, ...daily.map(d => d.volume));
    const nice = 10 ** Math.floor(Math.log10(max)); const top = Math.ceil(max / nice) * nice;
    const bw = (W - P.l - P.r) / daily.length;
    const y = v => P.t + (H - P.t - P.b) * (1 - v / top);
    const ticks = [0, top / 2, top];
    const short = v => { const m = v / 10 ** exp(currency); return m >= 1e7 ? (m / 1e7).toFixed(1) + "Cr" : m >= 1e5 ? (m / 1e5).toFixed(1) + "L" : m >= 1e3 ? (m / 1e3).toFixed(0) + "k" : m.toFixed(0); };
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Daily volume">
      ${ticks.map(t => `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${P.l - 8}" y="${y(t) + 4}" text-anchor="end">${short(t)}</text>`).join("")}
      ${daily.map((d, i) => { const h = Math.max(d.volume ? 2 : 0, y(0) - y(d.volume));
        return `<rect class="bar" x="${P.l + i * bw + bw * .18}" y="${y(0) - h}" width="${bw * .64}" height="${h}" rx="3"><title>${d.date}: ${currency} ${major(d.volume, currency)} · ${d.count} payments</title></rect>`; }).join("")}
      <text x="${P.l}" y="${H - 6}">${daily[0]?.date.slice(5) || ""}</text><text x="${W - P.r}" y="${H - 6}" text-anchor="end">${daily.at(-1)?.date.slice(5) || ""}</text></svg>`;
  }
  PAGES.overview = async () => {
    const [a, bal] = await Promise.all([api("/v1/analytics?days=30"), api("/v1/balance")]);
    const c = a.currency, b = bal.data.find(x => x.currency === c);
    const rateList = (rows) => rows.length ? `<div class="list-rows">${rows.map(r => `<div class="lr"><span class="method-pill">${methodIcon[r.name] || ""}${esc(methodName[r.name] || r.name)}</span>
      <span style="display:flex;align-items:center;gap:10px"><span class="meter"><i style="width:${r.success_rate}%"></i></span><b style="width:48px;text-align:right">${r.success_rate}%</b><span class="cell-sub" style="width:56px;text-align:right">${r.attempts} ${r.attempts === 1 ? "try" : "tries"}</span></span></div>`).join("")}</div>`
      : `<div class="card-body cell-sub">No attempts yet.</div>`;
    $("#page").innerHTML = head(`Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 17 ? "afternoon" : "evening"}, ${esc(merchant.name)}`, "Your business over the last 30 days.",
        `<button class="btn ghost" data-dl="payments">${I.download} Export payments</button>`) + `
      <div class="stats">
        <div class="card stat hero"><div class="lbl"><span class="ico-dot">${I.trend}</span>Net volume</div><div class="val">${money(b ? b.net_volume : 0, c)}</div>
          <div class="sub">${a.payments} payments · avg ${c} ${major(a.average_payment, c)}</div></div>
        <div class="card stat"><div class="lbl"><span class="ico-dot">${I.check}</span>Success rate</div><div class="val">${a.success_rate ?? "—"}${a.success_rate != null ? "%" : ""}</div><div class="sub">across all gateways</div></div>
        <div class="card stat"><div class="lbl"><span class="ico-dot">${I.sub}</span>MRR</div><div class="val">${money(a.mrr, c)}</div><div class="sub">${a.active_subscriptions} active · ${a.churned_subscriptions} churned</div></div>
        <div class="card stat"><div class="lbl"><span class="ico-dot">${I.invoice}</span>Outstanding</div><div class="val">${money(a.outstanding_invoices.amount, c)}</div><div class="sub">${a.outstanding_invoices.count} open invoices</div></div>
      </div>
      <div class="split"><div class="card"><div class="card-head"><h2>Daily volume</h2><span class="cell-sub">${esc(c)} · hover bars for detail</span></div><div class="card-body">${barChart(a.daily, c)}</div></div>
        <div class="card"><div class="card-head"><h2>At a glance</h2></div><div class="list-rows">
          <div class="lr"><span>Unique customers</span><b>${a.unique_customers}</b></div>
          <div class="lr"><span>Refunded</span><b>${c} ${major(a.refunded, c)}</b></div>
          <div class="lr"><span>Disputes lost</span><b>${c} ${major(b?.disputes_lost || 0, c)}</b></div>
          <div class="lr"><span>Payouts sent</span><b>${c} ${major(b?.payouts_sent || 0, c)}</b></div>
          <div class="lr"><span>Risk-flagged payments</span><b>${a.risk_flagged}</b></div></div></div></div>
      <div class="split" style="grid-template-columns:1fr 1fr">
        <div class="card"><div class="card-head"><h2>Success rate by method</h2></div>${rateList(a.by_method)}</div>
        <div class="card"><div class="card-head"><h2>Success rate by gateway</h2></div>${rateList(a.by_provider)}</div></div>
      <div class="card"><div class="card-head"><h2>Top failure reasons</h2></div>${a.top_failure_reasons.length ? `<div class="list-rows">${a.top_failure_reasons.map(f => `<div class="lr"><span>${esc(f.reason)}</span><b>${f.count}</b></div>`).join("")}</div>` : `<div class="card-body cell-sub">No failures. Nice.</div>`}</div>`;
    bind("[data-dl]", d => download(d.dl));
  };

  // ---------------------------------------------------------------- Payments
  PAGES.payments = async () => {
    const list = (await api("/v1/payment_intents?limit=100")).data;
    const paid = list.filter(p => ["succeeded", "partially_refunded", "refunded"].includes(p.status));
    const open = p => ["requires_payment_method", "requires_action"].includes(p.status);
    const groups = {all: list.length, succeeded: paid.length, pending_verification: list.filter(p => p.status === "pending_verification").length,
      open: list.filter(open).length, flagged: list.filter(p => p.risk?.level && p.risk.level !== "normal").length};
    const shown = list.filter(p => payFilter === "all" || (payFilter === "succeeded" ? paid.includes(p) : payFilter === "open" ? open(p)
      : payFilter === "flagged" ? p.risk?.level && p.risk.level !== "normal" : p.status === payFilter));
    $("#page").innerHTML = head("Payments", "Every payment across all your gateways, in one place.",
        `<button class="btn ghost" data-dl="payments">${I.download} CSV</button><button class="btn" id="newPay">${I.plus} New payment</button>`) + `
      <div class="card"><div class="card-head"><div class="chips">${[["all", "All"], ["succeeded", "Succeeded"], ["pending_verification", "Needs verification"], ["open", "Open"], ["flagged", "Risk flagged"]]
        .map(([k, l]) => `<button class="chip" data-f="${k}" aria-pressed="${payFilter === k}">${l}<span class="c">${groups[k]}</span></button>`).join("")}</div></div>
      ${shown.length ? table(["Amount", "Status", "Description", "Method", "Risk", "Created", ""], shown.map(p => `<tr>
        <td><span class="amt">${money(p.amount, p.currency)}</span></td><td>${badge(p.status)}</td>
        <td><div class="cell-main">${esc(p.description || "—")}</div><div class="mono">${esc(p.id)}</div></td>
        <td>${p.method ? `<span class="method-pill">${methodIcon[p.method] || ""}${methodName[p.method] || esc(p.method)}</span><div class="cell-sub">via ${esc(p.provider)}</div>` : `<span class="cell-sub">—</span>`}</td>
        <td>${p.risk ? `<span class="badge ${p.risk.level === "normal" ? "b-ok" : p.risk.level === "elevated" ? "b-warn" : "b-err"}" title="${esc((p.risk.reasons || []).join("; "))}">${p.risk.score ?? 0}</span>` : `<span class="cell-sub">—</span>`}</td>
        <td><div>${when(p.created)}</div></td><td><div class="actions">${payActions(p)}</div></td></tr>`))
        : empty(I.receipt, "No payments here yet", "Create a payment or share a payment link.")}</div>`;
    bind("[data-f]", d => { payFilter = d.f; reload(); });
    bind("[data-dl]", d => download(d.dl));
    $("#newPay").onclick = guard(async () => {
      const f = await modal({title: "New payment", sub: "Opens your hosted checkout in a new tab.", submit: "Create & open",
        body: field("Amount", `<div class="amount-input">${currencySelect()}<input name="amount" type="number" min="0.01" step="0.01" required placeholder="0.00"></div>`) +
              field("Description", `<input name="description" maxlength="500" placeholder="e.g. Order #1042">`) +
              field("Customer email <span class='cell-sub'>(optional)</span>", `<input name="email" type="email">`)});
      if (!f) return;
      const cur = f.get("currency");
      const pi = await post("/v1/payment_intents", {amount: toMinor(f.get("amount"), cur), currency: cur, description: f.get("description") || null,
        customer: f.get("email") ? {email: f.get("email")} : {}});
      window.open(pi.checkout_url, "_blank"); toast("Payment created"); reload();
    });
    bind("[data-act]", async d => {
      const p = list.find(x => x.id === d.id);
      if (d.act === "approve") {
        if (!await modal({title: "Approve UPI payment?", sub: `Approve only after you see ${p.currency} ${major(p.amount, p.currency)} with this UTR in your bank statement, or let Reconciliation do it for you.`, submit: "Mark as paid"})) return;
        await post(`/v1/payment_intents/${d.id}/verify`, {approve: true}); toast("Payment marked as paid");
      } else if (d.act === "reject") {
        const f = await modal({title: "Reject UPI payment", sub: "The customer will be asked to pay again.", submit: "Reject", danger: true,
          body: field("Reason shown to customer", `<input name="note" maxlength="300" placeholder="UTR not found in bank statement">`)});
        if (!f) return; await post(`/v1/payment_intents/${d.id}/verify`, {approve: false, note: f.get("note") || null}); toast("Payment rejected");
      } else if (d.act === "refund") {
        const left = p.amount - p.amount_refunded;
        const f = await modal({title: "Refund payment", sub: `Up to ${p.currency} ${major(left, p.currency)} can be refunded.`, submit: "Issue refund",
          body: field("Amount", `<div class="amount-input"><select disabled><option>${esc(p.currency)}</option></select><input name="amount" type="number" min="0.01" step="0.01" max="${left / 10 ** exp(p.currency)}" value="${(left / 10 ** exp(p.currency)).toFixed(exp(p.currency))}" required></div>`) +
            field("Reason", `<select name="reason"><option value="requested_by_customer">Requested by customer</option><option value="duplicate">Duplicate payment</option><option value="fraudulent">Fraudulent</option></select>`)});
        if (!f) return;
        const r = await post("/v1/refunds", {payment_intent: d.id, amount: toMinor(f.get("amount"), p.currency), reason: f.get("reason")});
        if (r.status === "manual_action_required") await modal({title: "Send this refund from your UPI app", cancel: "", submit: "Got it",
          sub: "Direct-UPI payments can't be reversed automatically. Send the money back (or use Payouts), then mark the refund completed."});
        else toast("Refund issued");
      } else if (d.act === "cancel") {
        if (!await modal({title: "Cancel this payment?", sub: "The customer won't be able to pay it anymore.", submit: "Cancel payment", danger: true, cancel: "Keep it"})) return;
        await post(`/v1/payment_intents/${d.id}/cancel`); toast("Payment canceled");
      } else if (d.act === "dispute") {
        await post(`/v1/test_helpers/payment_intents/${d.id}/dispute`, {}); toast("Test dispute opened"); return go("disputes");
      }
      reload();
    });
  };
  function payActions(p) {
    if (p.status === "pending_verification") return `<button class="btn sm success" data-act="approve" data-id="${p.id}">${I.check}Approve</button><button class="btn sm ghost" data-act="reject" data-id="${p.id}">Reject</button>`;
    if (["succeeded", "partially_refunded"].includes(p.status)) return `<button class="btn sm ghost" data-act="refund" data-id="${p.id}">${I.undo}Refund</button>` +
      (merchant.mode === "test" ? `<button class="btn sm ghost" data-act="dispute" data-id="${p.id}" title="Simulate a chargeback">${I.dispute}</button>` : "");
    if (["requires_payment_method", "requires_action"].includes(p.status)) return `<button class="btn sm ghost" data-act="cancel" data-id="${p.id}">Cancel</button>`;
    return "";
  }

  // ---------------------------------------------------------------- Payment links
  PAGES.links = async () => {
    const list = (await api("/v1/payment_links")).data;
    $("#page").innerHTML = head("Payment links", "Share a link or print a QR code: no website needed.", `<button class="btn" id="newLink">${I.plus} New link</button>`) +
      `<div class="card">${list.length ? table(["Link", "Price", "Collected", "Status", ""], list.map(l => `<tr>
        <td><div class="cell-main">${esc(l.title)}</div><div class="mono">${esc(l.url)}</div></td>
        <td>${l.amount ? `<span class="amt">${money(l.amount, l.currency)}</span>` : `<span class="cell-sub">Customer chooses${l.min_amount ? ` (min ${major(l.min_amount, l.currency)})` : ""}</span>`}</td>
        <td><span class="amt">${money(l.amount_collected, l.currency)}</span><div class="cell-sub">${l.payments_count} payment${l.payments_count === 1 ? "" : "s"}${l.max_payments ? ` of ${l.max_payments}` : ""}</div></td>
        <td>${l.active ? `<span class="badge b-ok">Active</span>` : `<span class="badge b-neutral">Closed</span>`}</td>
        <td><div class="actions"><button class="btn sm ghost" data-copy="${esc(l.url)}">Copy</button><button class="btn sm ghost" data-qr="${esc(l.url)}" data-title="${esc(l.title)}">${I.qr}QR</button>
          <a class="btn sm ghost" href="${esc(l.url)}" target="_blank" rel="noopener">${I.ext}</a>
          <button class="btn sm ghost" data-toggle="${l.id}" data-active="${l.active}">${l.active ? "Close" : "Reopen"}</button></div></td></tr>`))
        : empty(I.link, "No payment links yet", "Create one for a product, an event ticket, a donation or a fee.")}</div>`;
    bind("[data-copy]", d => copy(d.copy));
    bind("[data-qr]", async d => {
      await modal({title: esc(d.title), sub: "Print this and put it on your counter. Customers scan, pay, done.", submit: "Done", cancel: "",
        body: `<div class="qr-box" id="qrBox"></div><div class="copy"><code>${esc(d.qr)}</code></div>`,
        onOpen: () => window.QRCode ? new QRCode($("#qrBox"), {text: d.qr, width: 220, height: 220}) : ($("#qrBox").textContent = d.qr)});
    });
    bind("[data-toggle]", async d => { await api(`/v1/payment_links/${d.toggle}`, {method: "PATCH", body: {active: d.active !== "true"}}); reload(); });
    $("#newLink").onclick = guard(async () => {
      const f = await modal({title: "New payment link", submit: "Create link",
        body: field("Title", `<input name="title" required maxlength="200" placeholder="e.g. Yoga class – monthly pass">`) +
          field("Description <span class='cell-sub'>(optional)</span>", `<input name="description" maxlength="1000">`) +
          field("Price", `<div class="amount-input">${currencySelect()}<input name="amount" type="number" min="0.01" step="0.01" placeholder="Leave empty: customer chooses"></div>`) +
          `<div class="form-grid">${field("Minimum (if customer chooses)", `<input name="min_amount" type="number" min="0.01" step="0.01">`)}${field("Max payments", `<input name="max_payments" type="number" min="1" placeholder="Unlimited">`)}</div>`});
      if (!f) return;
      const cur = f.get("currency");
      const l = await post("/v1/payment_links", {title: f.get("title"), description: f.get("description") || null, currency: cur,
        amount: f.get("amount") ? toMinor(f.get("amount"), cur) : null, min_amount: f.get("min_amount") ? toMinor(f.get("min_amount"), cur) : null,
        max_payments: f.get("max_payments") ? +f.get("max_payments") : null});
      copy(l.url); reload();
    });
  };

  // ---------------------------------------------------------------- Reconciliation
  PAGES.recon = async () => {
    const runs = (await api("/v1/reconciliation")).data;
    $("#page").innerHTML = head("Reconciliation", "Upload your bank statement. Zero-fee UPI payments are matched and confirmed automatically.") + `
      <div class="secure-note">${I.recon}<div><b>How matching works:</b> each credit is matched by its 12-digit UTR and amount to payments waiting for verification. If a customer paid but never entered the UTR, the payment ID in the bank narration (sent as the UPI reference) is used. Anything ambiguous is left for you to review.</div></div>
      <div class="card" style="margin-bottom:18px"><div class="card-head"><h2>Upload statement</h2><span class="cell-sub">CSV export from any Indian bank's net banking</span></div>
        <div class="card-body" style="display:grid;gap:12px"><input type="file" id="file" accept=".csv,text/csv">
        <label style="display:flex;gap:8px;align-items:center;font-weight:600"><input type="checkbox" id="auto" checked style="width:auto"> Automatically confirm matched payments</label>
        <div><button class="btn" id="run">${I.recon} Reconcile</button></div></div></div>
      <div class="card"><div class="card-head"><h2>History</h2></div>${runs.length ? table(["When", "Rows", "Matched", "Needs review", "Unmatched", ""], runs.map(r => `<tr>
        <td>${when(r.created)}</td><td>${r.rows}</td><td><span class="badge b-ok">${r.matched}</span></td><td><span class="badge ${r.report.needs_review.length ? "b-warn" : "b-neutral"}">${r.report.needs_review.length}</span></td>
        <td><span class="badge b-neutral">${r.report.unmatched.length}</span></td><td><div class="actions"><button class="btn sm ghost" data-run="${r.id}">Details</button></div></td></tr>`))
        : empty(I.recon, "No reconciliations yet", "Upload today's statement to confirm UPI payments in one click.")}</div>`;
    $("#run").onclick = guard(async () => {
      const file = $("#file").files[0]; if (!file) throw new Error("Choose a CSV file first");
      const r = await post("/v1/reconciliation", {csv: await file.text(), auto_approve: $("#auto").checked});
      toast(`${r.matched} of ${r.rows} credits matched`); reload();
    });
    bind("[data-run]", async d => {
      const r = runs.find(x => x.id === d.run);
      const sec = (t, rows, fmt) => rows.length ? `<h4 style="margin:6px 0">${t}</h4>${rows.map(fmt).join("")}` : "";
      await modal({title: "Reconciliation details", submit: "Close", cancel: "", body:
        sec("Matched", r.report.matched, m => `<div class="cell-sub">${esc(m.payment_intent)} · by ${esc(m.matched_by)} · ${esc(m.ref)}</div>`) +
        sec("Needs review", r.report.needs_review, m => `<div class="cell-sub" style="color:var(--warn)">${esc(m.payment_intent || "")} ${esc(m.utr || "")}: ${esc(m.reason)}</div>`) +
        sec("Unmatched credits", r.report.unmatched.slice(0, 50), m => `<div class="cell-sub">${major(m.amount, "INR")} · ${esc(m.text)}</div>`) || "Nothing to show."});
    });
  };

  // ---------------------------------------------------------------- Disputes
  PAGES.disputes = async () => {
    const list = (await api("/v1/disputes")).data;
    $("#page").innerHTML = head("Disputes", "Chargebacks from every gateway. Respond before the deadline.") +
      `<div class="card">${list.length ? table(["Amount", "Status", "Reason", "Payment", "Respond by", ""], list.map(d => `<tr>
        <td><span class="amt">${money(d.amount, d.currency)}</span></td><td>${badge(d.status)}</td><td>${esc(d.reason.replaceAll("_", " "))}</td>
        <td class="mono">${esc(d.payment_intent)}</td><td>${d.due_by ? `${I.clock} ${date(d.due_by)}` : "—"}</td>
        <td><div class="actions">${d.status === "needs_response" ? `<button class="btn sm" data-ev="${d.id}">Submit evidence</button><button class="btn sm ghost" data-accept="${d.id}">Accept</button>` : ""}</div></td></tr>`))
        : empty(I.dispute, "No disputes", "When a customer disputes a payment with their bank, it shows up here.")}</div>`;
    bind("[data-ev]", async d => {
      const f = await modal({title: "Submit evidence", sub: "Sent to the gateway and the customer's bank.", submit: "Submit",
        body: field("Summary", `<textarea class="textarea" name="summary" required minlength="10" placeholder="What was sold, when it was delivered, and why the charge is valid."></textarea>`) +
          field("Tracking number", `<input name="shipping_tracking_number">`) + field("Customer email", `<input name="customer_email_address" type="email">`)});
      if (!f) return;
      const body = Object.fromEntries([...f].filter(([, v]) => v));
      await post(`/v1/disputes/${d.ev}/evidence`, body); toast("Evidence submitted"); reload();
    });
    bind("[data-accept]", async d => {
      if (!await modal({title: "Accept this dispute?", sub: "The amount goes back to the customer and the dispute closes as lost.", submit: "Accept", danger: true})) return;
      await post(`/v1/disputes/${d.accept}/accept`); reload();
    });
  };

  // ---------------------------------------------------------------- Customers
  PAGES.customers = async () => {
    const list = (await api("/v1/customers")).data;
    $("#page").innerHTML = head("Customers", "People and businesses you bill.", `<button class="btn" id="newCus">${I.plus} New customer</button>`) +
      `<div class="card">${list.length ? table(["Customer", "GSTIN", "State", "Phone", "Added"], list.map(c => `<tr>
        <td><div class="cell-main">${esc(c.name || c.email || c.id)}</div><div class="cell-sub">${esc(c.email || "")}</div></td>
        <td class="mono">${esc(c.gstin || "—")}</td><td>${esc(c.state_code || "—")}</td><td>${esc(c.phone || "—")}</td><td>${when(c.created)}</td></tr>`))
        : empty(I.users, "No customers yet", "Add customers to send GST invoices and start subscriptions.")}</div>`;
    $("#newCus").onclick = guard(async () => { if (await customerModal()) reload(); });
  };
  async function customerModal() {
    const f = await modal({title: "New customer", submit: "Add customer",
      body: `<div class="form-grid">${field("Name", `<input name="name" maxlength="120">`)}${field("Email", `<input name="email" type="email">`)}</div>
        <div class="form-grid">${field("Phone", `<input name="phone" maxlength="20">`)}${field("GSTIN <span class='cell-sub'>(B2B)</span>", `<input name="gstin" maxlength="15" style="text-transform:uppercase">`)}</div>
        ${field("State code", `<input name="state_code" maxlength="2" placeholder="e.g. 27 for Maharashtra">`, "Decides CGST+SGST vs IGST. Taken from the GSTIN when given.")}
        ${field("Billing address", `<input name="address" maxlength="500">`)}`});
    if (!f) return null;
    const body = Object.fromEntries([...f].filter(([, v]) => v).map(([k, v]) => [k, k === "gstin" ? v.toUpperCase() : v]));
    const c = await post("/v1/customers", body); toast("Customer added"); return c;
  }

  // ---------------------------------------------------------------- Invoices
  PAGES.invoices = async () => {
    const [list, customers] = await Promise.all([api("/v1/invoices"), api("/v1/customers")]);
    const cname = Object.fromEntries(customers.data.map(c => [c.id, c.name || c.email]));
    $("#page").innerHTML = head("Invoices", "GST-compliant invoices with a pay button built in.",
        `<button class="btn ghost" data-dl="invoices">${I.download} CSV</button><button class="btn" id="newInv">${I.plus} New invoice</button>`) +
      `<div class="card">${list.data.length ? table(["Number", "Customer", ">Total", "Tax", "Status", "Due", ""], list.data.map(i => `<tr>
        <td><div class="cell-main">${esc(i.number || "Draft")}</div><div class="mono">${esc(i.id)}</div></td><td>${esc(cname[i.customer] || "—")}</td>
        <td class="num"><span class="amt">${money(i.total, i.currency)}</span></td><td class="cell-sub">${i.tax_breakdown.map(t => esc(t.name)).join(" + ") || "—"}</td>
        <td>${badge(i.status)}</td><td>${i.status === "open" ? date(i.due_date) : i.paid_at ? date(i.paid_at) : "—"}</td>
        <td><div class="actions"><a class="btn sm ghost" href="${esc(i.hosted_invoice_url)}" target="_blank" rel="noopener">${I.ext}View</a>
          <button class="btn sm ghost" data-copy="${esc(i.hosted_invoice_url)}">Copy link</button>
          ${i.status === "open" ? `<button class="btn sm ghost" data-paid="${i.id}">Mark paid</button><button class="btn sm ghost" data-void="${i.id}">Void</button>` : ""}</div></td></tr>`))
        : empty(I.invoice, "No invoices yet", "Create a GST invoice and share its link. Customers pay with UPI, cards or net banking.")}</div>`;
    bind("[data-dl]", d => download(d.dl));
    bind("[data-copy]", d => copy(d.copy));
    bind("[data-void]", async d => { if (await modal({title: "Void invoice?", sub: "The customer can no longer pay it.", submit: "Void", danger: true})) { await post(`/v1/invoices/${d.void}/void`); reload(); } });
    bind("[data-paid]", async d => {
      const f = await modal({title: "Mark as paid", sub: "For cash, cheque or a bank transfer received outside SangamPay.", submit: "Mark paid", body: field("Note", `<input name="note" placeholder="e.g. Cheque #123456">`)});
      if (f) { await post(`/v1/invoices/${d.paid}/mark_paid`, {note: f.get("note")}); reload(); }
    });
    $("#newInv").onclick = guard(async () => {
      const row = () => `<div class="li-row"><input name="desc" placeholder="Item" required><input name="qty" type="number" min="1" value="1"><input name="price" type="number" min="0" step="0.01" placeholder="Rate" required>
        <select name="tax">${[0, 5, 12, 18, 28].map(r => `<option value="${r}" ${r === 18 ? "selected" : ""}>${r}%</option>`).join("")}</select><button type="button" class="btn sm ghost" data-rm>✕</button></div>`;
      const f = await modal({title: "New invoice", sub: merchant.profile.gstin ? `Tax invoice from GSTIN ${esc(merchant.profile.gstin)}` : "Add your GSTIN in Settings to issue GST tax invoices.", submit: "Create & finalize",
        body: field("Customer", `<select name="customer"><option value="">— No customer —</option>${customers.data.map(c => `<option value="${c.id}">${esc(c.name || c.email)}${c.gstin ? " · " + c.gstin : ""}</option>`).join("")}</select>`) +
          field("Currency", currencySelect()) + `<div class="field"><label>Line items <span class="cell-sub">(description · qty · rate · GST)</span></label><div class="line-items" id="lines">${row()}</div>
          <button type="button" class="btn sm ghost" id="addLine" style="width:max-content">${I.plus} Add item</button></div>` +
          `<div class="form-grid">${field("Due in (days)", `<input name="due" type="number" min="0" value="7">`)}${field("Notes", `<input name="notes" placeholder="Thank you for your business!">`)}</div>`,
        onOpen: () => { $("#addLine").onclick = () => $("#lines").insertAdjacentHTML("beforeend", row());
          $("#lines").onclick = e => { if (e.target.closest("[data-rm]") && $$("#lines .li-row").length > 1) e.target.closest(".li-row").remove(); }; }});
      if (!f) return;
      const cur = f.get("currency"), d = f.getAll("desc"), q = f.getAll("qty"), pr = f.getAll("price"), t = f.getAll("tax");
      const inv = await post("/v1/invoices", {customer: f.get("customer") || null, currency: cur, due_days: +f.get("due"), notes: f.get("notes") || null,
        line_items: d.map((x, i) => ({description: x, quantity: +q[i] || 1, unit_amount: toMinor(pr[i] || 0, cur), tax_rate: +t[i]}))});
      toast(`Invoice ${inv.number} created`); window.open(inv.hosted_invoice_url, "_blank"); reload();
    });
  };

  // ---------------------------------------------------------------- Subscriptions
  PAGES.subscriptions = async () => {
    const [subs, plans, customers] = await Promise.all([api("/v1/subscriptions"), api("/v1/plans"), api("/v1/customers")]);
    const pmap = Object.fromEntries(plans.data.map(p => [p.id, p])), cmap = Object.fromEntries(customers.data.map(c => [c.id, c]));
    const every = p => `${p.interval_count > 1 ? p.interval_count + " " + p.interval + "s" : p.interval}`;
    $("#page").innerHTML = head("Subscriptions", "Recurring billing with UPI AutoPay, e-NACH, card mandates, or an invoice every cycle.",
        `<button class="btn ghost" id="newPlan">${I.plus} New plan</button><button class="btn" id="newSub">${I.plus} New subscription</button>`) + `
      <div class="card" style="margin-bottom:18px"><div class="card-head"><h2>Plans</h2></div>${plans.data.length ? table(["Plan", "Price", "Trial", "GST", ""], plans.data.map(p => `<tr>
        <td><div class="cell-main">${esc(p.name)}</div><div class="mono">${esc(p.id)}</div></td><td><span class="amt">${money(p.amount, p.currency)}</span> <span class="cell-sub">/ ${every(p)}</span></td>
        <td>${p.trial_days ? p.trial_days + " days" : "—"}</td><td>${p.tax_rate ? p.tax_rate + "%" : "—"}</td><td>${p.active ? "" : `<span class="badge b-neutral">Archived</span>`}</td></tr>`))
        : empty(I.sub, "No plans yet", "Create a plan, e.g. ₹499 every month with a 14-day trial.")}</div>
      <div class="card"><div class="card-head"><h2>Subscriptions</h2></div>${subs.data.length ? table(["Customer", "Plan", "Status", "Collection", "Renews", ""], subs.data.map(s => `<tr>
        <td><div class="cell-main">${esc(cmap[s.customer]?.name || cmap[s.customer]?.email || s.customer)}</div><div class="mono">${esc(s.id)}</div></td>
        <td>${esc(pmap[s.plan]?.name || s.plan)}${s.quantity > 1 ? ` × ${s.quantity}` : ""}</td><td>${s.status === "incomplete" && s.collection === "invoice" ? `<span class="badge b-neutral">Awaiting payment</span>` : badge(s.status)}${s.cancel_at_period_end ? ` <span class="cell-sub">ends at period end</span>` : ""}</td>
        <td>${s.collection === "mandate" ? `<span class="method-pill">${methodIcon[s.mandate.method] || ""}${({upi: "UPI AutoPay", emandate: "e-NACH", card: "Card mandate", bank_debit: "Bank debit"})[s.mandate.method] || ""}</span><div class="cell-sub">${esc(s.mandate.provider)} · ${esc(s.mandate.status)}</div>` : `<span class="cell-sub">Invoice each cycle</span>`}</td>
        <td>${s.status === "canceled" ? "—" : date(s.current_period_end)}</td>
        <td><div class="actions">${s.status === "incomplete" && s.mandate?.authorization_url ? `<button class="btn sm ghost" data-copy="${esc(s.mandate.authorization_url)}">Copy mandate link</button>` : ""}
          ${merchant.mode === "test" && s.status !== "canceled" ? `<button class="btn sm ghost" data-adv="${s.id}" title="Test mode: fast-forward time">${I.clock} +1 month</button>` : ""}
          ${s.status !== "canceled" ? `<button class="btn sm ghost" data-cancel="${s.id}">Cancel</button>` : ""}</div></td></tr>`))
        : empty(I.sub, "No subscriptions yet", "Start one for a customer; they approve the mandate once and are billed automatically.")}</div>`;
    bind("[data-copy]", d => copy(d.copy));
    bind("[data-adv]", async d => { const r = await post(`/v1/test_helpers/subscriptions/${d.adv}/advance`, {days: 31});
      toast(`Billing run: ${r.billing_run.renewed} renewed, ${r.billing_run.charged} charged, ${r.billing_run.failed} failed`); reload(); });
    bind("[data-cancel]", async d => {
      const f = await modal({title: "Cancel subscription", submit: "Cancel subscription", danger: true, cancel: "Keep it",
        body: `<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="end" value="1" checked style="width:auto"> Cancel at the end of the current period</label>`});
      if (!f) return; await post(`/v1/subscriptions/${d.cancel}/cancel`, {at_period_end: f.get("end") === "1"}); toast("Subscription updated"); reload();
    });
    $("#newPlan").onclick = guard(async () => {
      const f = await modal({title: "New plan", submit: "Create plan",
        body: field("Name", `<input name="name" required placeholder="e.g. Pro monthly">`) +
          field("Price", `<div class="amount-input">${currencySelect()}<input name="amount" type="number" min="0.01" step="0.01" required></div>`) +
          `<div class="form-grid">${field("Every", `<div style="display:flex;gap:6px"><input name="count" type="number" min="1" max="12" value="1" style="width:70px"><select name="interval"><option value="month">month(s)</option><option value="year">year(s)</option><option value="week">week(s)</option><option value="day">day(s)</option></select></div>`)}
          ${field("Free trial (days)", `<input name="trial" type="number" min="0" value="0">`)}</div>
          <div class="form-grid">${field("GST rate", `<select name="tax">${[0, 5, 12, 18, 28].map(r => `<option ${r === 18 ? "selected" : ""}>${r}</option>`).join("")}</select>`)}${field("HSN/SAC", `<input name="hsn" maxlength="10" placeholder="e.g. 998314">`)}</div>`});
      if (!f) return;
      const cur = f.get("currency");
      await post("/v1/plans", {name: f.get("name"), amount: toMinor(f.get("amount"), cur), currency: cur, interval: f.get("interval"),
        interval_count: +f.get("count"), trial_days: +f.get("trial"), tax_rate: +f.get("tax"), hsn_sac: f.get("hsn") || null});
      toast("Plan created"); reload();
    });
    $("#newSub").onclick = guard(async () => {
      if (!plans.data.some(p => p.active)) throw new Error("Create a plan first");
      if (!customers.data.length) { if (!await customerModal()) return; return reload(); }
      const f = await modal({title: "New subscription", submit: "Create subscription",
        body: field("Customer", `<select name="customer">${customers.data.map(c => `<option value="${c.id}">${esc(c.name || c.email)}</option>`).join("")}</select>`) +
          field("Plan", `<select name="plan">${plans.data.filter(p => p.active).map(p => `<option value="${p.id}">${esc(p.name)} · ${p.currency} ${major(p.amount, p.currency)} / ${every(p)}</option>`).join("")}</select>`) +
          field("How to collect", `<select name="how"><option value="upi">UPI AutoPay mandate</option><option value="emandate">e-NACH bank mandate</option><option value="card">Card mandate</option><option value="bank_debit">ACH / SEPA debit</option><option value="invoice">Send an invoice every cycle (any method, incl. zero-fee UPI)</option></select>`,
            "Mandates debit automatically after one approval. Invoices are emailed via your webhook and paid manually.") +
          field("Quantity", `<input name="qty" type="number" min="1" value="1">`)});
      if (!f) return;
      const how = f.get("how");
      const s = await post("/v1/subscriptions", {customer: f.get("customer"), plan: f.get("plan"), quantity: +f.get("qty"),
        collection: how === "invoice" ? "invoice" : "mandate", mandate_method: how === "invoice" ? null : how});
      if (s.mandate?.authorization_url) {
        await modal({title: "Send this link to your customer", sub: "They approve the mandate once in their UPI app or bank. Billing starts automatically.", submit: "Open link", cancel: "Close",
          body: `<div class="copy"><code>${esc(s.mandate.authorization_url)}</code><button type="button" id="cp">Copy</button></div>`,
          onOpen: () => $("#cp").onclick = () => copy(s.mandate.authorization_url)}).then(ok => ok && window.open(s.mandate.authorization_url, "_blank"));
      } else toast("Subscription created");
      reload();
    });
  };

  // ---------------------------------------------------------------- Payouts & splits
  PAGES.payouts = async () => {
    const [list, banks, transfers] = await Promise.all([api("/v1/payouts"), api("/v1/bank_accounts"), api("/v1/transfers")]);
    const bmap = Object.fromEntries(banks.data.map(b => [b.id, b]));
    $("#page").innerHTML = head("Payouts & splits", "Pay vendors, sellers and refunds straight to bank accounts or UPI IDs.", `<button class="btn" id="newPo">${I.payout} Send payout</button>`) + `
      <div class="card" style="margin-bottom:18px"><div class="card-head"><h2>Payouts</h2><span class="cell-sub">via RazorpayX${merchant.mode === "test" ? " (sandbox in test mode)" : ""}</span></div>
      ${list.data.length ? table(["Amount", "Status", "To", "Mode", "Purpose", "When"], list.data.map(p => { const b = bmap[p.bank_account]; return `<tr>
        <td><span class="amt">${money(p.amount, p.currency)}</span></td><td>${badge(p.status)}${p.failure_reason ? `<div class="cell-sub">${esc(p.failure_reason)}</div>` : ""}</td>
        <td><div class="cell-main">${esc(b?.details.account_holder_name || "—")}</div><div class="mono">${esc(b ? (b.details.account_number || b.details.vpa || b.details.iban) : p.bank_account)}</div></td>
        <td>${esc(p.mode)}</td><td>${esc(p.purpose)}</td><td>${when(p.created)}</td></tr>`; }))
        : empty(I.payout, "No payouts yet", "Add a bank account in the vault, then send money in seconds with IMPS, NEFT, RTGS or UPI.")}</div>
      <div class="card"><div class="card-head"><h2>Marketplace splits</h2><span class="cell-sub">Pass <code>transfers</code> when creating a payment</span></div>
      ${transfers.data.length ? table(["Amount", "Status", "Destination", "Payment", "When"], transfers.data.map(t => `<tr><td><span class="amt">${money(t.amount, t.currency)}</span></td>
        <td>${badge(t.status)}</td><td class="mono">${esc(t.destination)}</td><td class="mono">${esc(t.payment_intent)}</td><td>${when(t.created)}</td></tr>`))
        : `<div class="card-body cell-sub">Split one payment between sellers automatically (Razorpay Route / Stripe Connect). Example: <code>"transfers": [{"account": "acc_Seller1", "amount": 80000}]</code></div>`}</div>`;
    $("#newPo").onclick = guard(async () => {
      if (!banks.data.length) { toast("Add a bank account first", true); return go("banks"); }
      const f = await modal({title: "Send payout", submit: "Send payout",
        body: field("To", `<select name="ba">${banks.data.map(b => `<option value="${b.id}">${esc(b.details.account_holder_name)} · ${esc(b.details.account_number || b.details.vpa || b.details.iban)}</option>`).join("")}</select>`) +
          field("Amount (INR)", `<input name="amount" type="number" min="1" step="0.01" required>`) +
          `<div class="form-grid">${field("Mode", `<select name="mode"><option>IMPS</option><option>NEFT</option><option>RTGS</option><option>UPI</option></select>`)}
           ${field("Purpose", `<select name="purpose"><option>payout</option><option>vendor bill</option><option>refund</option><option>cashback</option><option>salary</option></select>`)}</div>` +
          field("Narration", `<input name="narration" maxlength="30" placeholder="Shows on their statement">`)});
      if (!f) return;
      const p = await post("/v1/payouts", {bank_account: f.get("ba"), amount: toMinor(f.get("amount"), "INR"), mode: f.get("mode"), purpose: f.get("purpose"), narration: f.get("narration") || null});
      toast(`Payout ${p.status}`, p.status === "failed"); reload();
    });
  };

  // ---------------------------------------------------------------- Bank vault
  const BANK_KIND = {in_bank: ["India bank account", ["account_holder_name", "account_number", "ifsc"]], upi_vpa: ["UPI ID", ["account_holder_name", "vpa"]],
    iban: ["IBAN (Europe & more)", ["account_holder_name", "iban", "bic"]], us_ach: ["US bank account", ["account_holder_name", "routing_number", "account_number"]]};
  PAGES.banks = async () => {
    const list = await api("/v1/bank_accounts");
    $("#page").innerHTML = head("Bank vault", "Settlement, vendor and refund accounts, encrypted at rest.", `<button class="btn" id="addBank">${I.plus} Add bank account</button>`) +
      `<div class="secure-note">${I.lock}<div><b>Write-only by design.</b> Every record is sealed with its own AES-256-GCM key. Nobody can read the full number back through the API, only the masked version. It's decrypted only at the moment of a payout, inside the server.</div></div>
      <div class="card">${list.data.length ? table(["Account", "Type", "Owner", "Added", ""], list.data.map(b => { const d = b.details, primary = d.account_number || d.iban || d.vpa, secondary = d.ifsc || d.routing_number || d.bic || "";
        return `<tr><td><div class="vault-row"><div class="bank-ico">${I.bank}</div><div><div class="cell-main">${esc(d.account_holder_name)}</div><div class="mono">${esc(primary)}${secondary ? " · " + esc(secondary) : ""}</div></div></div></td>
          <td>${BANK_KIND[b.kind]?.[0] || esc(b.kind)}</td><td><span class="badge ${b.owner === "merchant" ? "b-info" : "b-neutral"}">${b.owner === "merchant" ? "Business" : "Customer"}</span></td>
          <td>${when(b.created)}</td><td><div class="actions"><button class="btn sm ghost" data-rm="${b.id}">${I.trash}Delete</button></div></td></tr>`; }))
        : empty(I.vault, "The vault is empty", "Add a vendor's or seller's account to send payouts, or a customer's account for refunds.")}</div>`;
    $("#addBank").onclick = guard(async () => {
      const p = modal({title: "Add bank account", sub: "Details are encrypted before they're saved.", submit: "Encrypt & save",
        body: field("Account type", `<select name="kind" id="kindSel">${Object.entries(BANK_KIND).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select>`) +
          field("Belongs to", `<select name="owner"><option value="merchant">My business / vendor / seller</option><option value="customer">A customer (refunds)</option></select>`) + `<div id="bankFields" style="display:grid;gap:14px"></div>`,
        onOpen: () => { const draw = () => $("#bankFields").innerHTML = BANK_KIND[$("#kindSel").value][1].map(f => field(f.replaceAll("_", " "), `<input name="d_${f}" ${f !== "bic" ? "required" : ""} autocomplete="off">`)).join("");
          $("#kindSel").onchange = draw; draw(); }});
      const f = await p; if (!f) return;
      const details = {}; for (const [k, v] of f) if (k.startsWith("d_")) details[k.slice(2)] = v;
      await post("/v1/bank_accounts", {kind: f.get("kind"), owner: f.get("owner"), details}); toast("Encrypted & saved"); reload();
    });
    bind("[data-rm]", async d => {
      if (!await modal({title: "Delete this account?", sub: "Its encryption key is destroyed with it, so it can never be recovered.", submit: "Delete permanently", danger: true})) return;
      await api(`/v1/bank_accounts/${d.rm}`, {method: "DELETE"}); toast("Deleted"); reload();
    });
  };

  // ---------------------------------------------------------------- Gateways & routing
  const GW_STYLE = {upi_direct: ["#0f9d58", "UPI", "Zero-fee UPI to your own UPI ID"], razorpay: ["#0b5cff", "R", "UPI, net banking, cards, AutoPay, e-NACH"],
    stripe: ["#635bff", "S", "Cards, ACH/SEPA, subscriptions worldwide"], paypal: ["#003087", "P", "PayPal wallets in 200+ countries"], razorpayx: ["#1a2b5f", "X", "Payouts by IMPS, NEFT, RTGS, UPI"]};
  const OPTIONAL = {stripe: ["webhook_secret"], razorpay: ["webhook_secret"], paypal: ["webhook_id"], razorpayx: ["webhook_secret"]};
  PAGES.gateways = async () => {
    const [prov, m, health] = await Promise.all([api("/v1/providers"), api("/v1/merchant"), api("/v1/routing/health")]);
    const strategies = [["priority", "Priority", "Your order, first to last"], ["success_rate", "Success rate", "Best-performing gateway first, learned live"], ["cost", "Lowest cost", "Cheapest gateway first (₹0 UPI wins)"]];
    $("#page").innerHTML = head("Gateways & routing", "Connect your own accounts. Payments land directly in them.") + `
      <div class="gateways">${prov.data.filter(p => GW_STYLE[p.name]).map(p => { const [color, mark, tagline] = GW_STYLE[p.name]; const fields = [
          ...p.config_fields.map(f => ({f, kind: "config", val: p.config[f] || ""})), ...p.credential_fields.map(f => ({f, kind: "credentials", secret: true})),
          ...(OPTIONAL[p.name] || []).map(f => ({f, kind: "credentials", secret: true, opt: true}))];
        return `<div class="card gw"><div class="gw-head"><div class="gw-logo" style="background:${color}">${mark}</div><div style="flex:1"><div class="gw-title">${esc(p.display_name)}</div><div class="gw-sub">${tagline}</div></div>
          ${p.connected ? `<span class="badge b-ok">Connected</span>` : `<span class="badge b-neutral">Not connected</span>`}</div>
          <div class="gw-body">${p.methods.length ? `<div class="tags">${p.methods.map(x => `<span>${methodName[x] || x}</span>`).join("")}</div>` : ""}
          ${fields.map(x => field(`${x.f.replaceAll("_", " ")}${x.opt ? ' <span class="cell-sub">(optional)</span>' : ""}`, `<input data-p="${p.name}" data-kind="${x.kind}" data-f="${x.f}" ${x.secret ? `type="password" placeholder="${p.connected ? "•••••••• saved" : ""}"` : ""} value="${esc(x.val || "")}" autocomplete="off">`)).join("")}
          ${p.webhook_url ? field(`Webhook URL <span class="cell-sub">(paste in ${esc(p.display_name)})</span>`, `<div class="copy"><code>${esc(p.webhook_url)}</code><button data-copy="${esc(p.webhook_url)}">Copy</button></div>`) : ""}</div>
          <div class="gw-foot">${p.connected ? `<button class="btn sm ghost" data-del="${p.name}">Disconnect</button>` : "<span></span>"}<button class="btn sm" data-save="${p.name}">${p.connected ? "Update" : "Connect"}</button></div></div>`; }).join("")}</div>
      <div class="card" style="margin-top:20px"><div class="card-head"><div><h2>Smart routing</h2><div class="cell-sub">If a gateway errors, the next one takes over automatically. A failing gateway is paused for 60s.</div></div>
        <div class="seg">${strategies.map(([k, l, t]) => `<button data-strategy="${k}" aria-pressed="${m.routing_strategy === k}" title="${t}">${l}</button>`).join("")}</div></div>
        ${Object.entries(m.routing).map(([k, v]) => `<div class="route"><div class="route-name"><span class="method-pill">${methodIcon[k] || ""}${methodName[k] || k}</span></div>
          <div class="flow">${v.map((g, i) => `${i ? '<span class="arrow">→</span>' : ""}<span class="node">${esc(g)}</span>`).join("")}${merchant.mode === "test" ? '<span class="arrow">→</span><span class="node" style="opacity:.6">mock (test)</span>' : ""}</div></div>`).join("")}
        <div class="card-body"><button class="btn sm ghost" id="editRoutes">Edit priority order</button></div></div>
      <div class="card" style="margin-top:20px"><div class="card-head"><h2>Live gateway health</h2><span class="cell-sub">Recent success rate per gateway &amp; method</span></div>
        ${health.data.length ? table(["Gateway", "Method", "Success rate", "Recent attempts", "Circuit"], health.data.map(h => `<tr><td>${esc(h.provider)}</td><td>${methodName[h.method] || esc(h.method)}</td>
          <td><span style="display:flex;align-items:center;gap:10px"><span class="meter"><i style="width:${h.success_rate}%"></i></span>${h.success_rate}%</span></td><td>${h.recent_attempts}</td>
          <td>${h.circuit_open ? `<span class="badge b-err">Paused</span>` : `<span class="badge b-ok">Healthy</span>`}</td></tr>`)) : `<div class="card-body cell-sub">Health data appears after the first payments.</div>`}</div>`;
    bind("[data-save]", async d => {
      const body = {credentials: {}, config: {}};
      $$(`[data-p="${d.save}"]`).forEach(i => { if (i.value.trim()) body[i.dataset.kind][i.dataset.f] = i.value.trim(); });
      await put(`/v1/providers/${d.save}`, body); toast("Gateway connected"); reload();
    });
    bind("[data-del]", async d => { if (!await modal({title: "Disconnect gateway?", sub: "Saved credentials are erased. Past payments are kept.", submit: "Disconnect", danger: true})) return;
      await api(`/v1/providers/${d.del}`, {method: "DELETE"}); toast("Disconnected"); reload(); });
    bind("[data-copy]", d => copy(d.copy));
    bind("[data-strategy]", async d => { await put("/v1/merchant/settings", {routing_strategy: d.strategy}); toast("Routing strategy updated"); reload(); });
    $("#editRoutes").onclick = guard(async () => {
      const f = await modal({title: "Edit priority order", sub: "Comma-separated gateway names, first choice first.", submit: "Save",
        body: Object.entries(m.routing).map(([k, v]) => field(methodName[k] || k, `<input name="${k}" value="${esc(v.join(", "))}">`)).join("")});
      if (!f) return;
      const routing = {}; for (const [k, v] of f) routing[k] = v.split(",").map(s => s.trim()).filter(Boolean);
      await put("/v1/merchant/routing", {routing}); toast("Routing saved"); reload();
    });
  };

  // ---------------------------------------------------------------- Risk
  PAGES.risk = async () => {
    const r = (await api("/v1/merchant")).risk_rules;
    const list = v => (v || []).join("\n");
    $("#page").innerHTML = head("Risk rules", "Stop fraud before it costs you. Every payment is scored when the customer pays.") + `
      <div class="kv">
        <div class="card"><div class="card-head"><h2>Limits</h2></div><div class="card-body" style="display:grid;gap:14px">
          ${field("Maximum payment amount (₹)", `<input id="max" type="number" min="1" value="${r.max_amount ? r.max_amount / 100 : ""}" placeholder="No limit">`)}
          ${field("Max attempts per customer email / hour", `<input id="pe" type="number" min="1" value="${r.max_attempts_per_email_hour}">`)}
          ${field("Max attempts per IP address / hour", `<input id="pi" type="number" min="1" value="${r.max_attempts_per_ip_hour}">`)}</div></div>
        <div class="card"><div class="card-head"><h2>Score thresholds</h2></div><div class="card-body" style="display:grid;gap:14px">
          ${field("Flag for review at score", `<input id="rs" type="number" min="0" max="100" value="${r.review_score}">`, "Flagged payments go through but are marked and trigger a payment_intent.risk_flagged webhook.")}
          ${field("Block at score", `<input id="bs" type="number" min="1" max="101" value="${r.block_score}">`, "Signals: repeated failures, unusually large amounts, IP velocity, missing email.")}
          ${field("Only accept customers from (country codes)", `<input id="ac" value="${esc((r.allowed_countries || []).join(", "))}" placeholder="All countries, e.g. IN, US, AE">`)}</div></div>
        <div class="card"><div class="card-head"><h2>Blocklists</h2></div><div class="card-body" style="display:grid;gap:14px">
          ${field("Blocked emails or @domains", `<textarea class="textarea" id="be" placeholder="fraudster@example.com&#10;@tempmail.com">${esc(list(r.block_emails))}</textarea>`)}
          ${field("Blocked IP addresses", `<textarea class="textarea" id="bi" placeholder="One per line">${esc(list(r.block_ips))}</textarea>`)}</div></div></div>
      <div style="margin-top:16px"><button class="btn" id="saveRisk">Save rules</button></div>`;
    $("#saveRisk").onclick = guard(async () => {
      const lines = id => $(id).value.split(/\n|,/).map(s => s.trim()).filter(Boolean);
      const body = {max_attempts_per_email_hour: +$("#pe").value, max_attempts_per_ip_hour: +$("#pi").value, review_score: +$("#rs").value, block_score: +$("#bs").value,
        allowed_countries: lines("#ac").map(s => s.toUpperCase()), block_emails: lines("#be"), block_ips: lines("#bi")};
      if ($("#max").value) body.max_amount = Math.round(+$("#max").value * 100);
      await put("/v1/merchant/risk_rules", body); toast("Risk rules saved");
    });
  };

  // ---------------------------------------------------------------- Developers
  PAGES.developers = async () => {
    const [keys, eps, evs, logs] = await Promise.all([api("/v1/api_keys"), api("/v1/webhook_endpoints"), api("/v1/events?limit=25"), api("/v1/audit_logs?limit=30")]);
    const evBadge = t => t.endsWith("succeeded") || t.endsWith("paid") || t.endsWith("activated") ? "b-ok" : /failed|duplicate|blocked|lost|unpaid/.test(t) ? "b-err" : /verification|past_due|flagged|dispute/.test(t) ? "b-warn" : "b-neutral";
    $("#page").innerHTML = head("Developers", "Team keys with roles, webhooks, event log and audit trail.",
        `<a class="btn ghost" href="/docs" target="_blank" rel="noopener">${I.ext} API reference</a>`) + `
      <div class="card" style="margin-bottom:18px"><div class="card-head"><div><h2>Team &amp; API keys</h2><div class="cell-sub">Give each teammate or service its own key and the least access it needs.</div></div><button class="btn sm" id="newKey">${I.plus} New key</button></div>
        ${table(["Name", "Role", "Key", "Last used", ""], keys.data.map(k => `<tr><td class="cell-main">${esc(k.name)}</td><td><span class="badge ${k.role === "admin" ? "b-info" : "b-neutral"}">${esc(k.role)}</span></td>
          <td class="mono">${esc(k.display)}</td><td>${k.revoked ? `<span class="badge b-err">Revoked</span>` : when(k.last_used)}</td>
          <td><div class="actions">${!k.revoked ? `<button class="btn sm ghost" data-revoke="${k.id}">Revoke</button>` : ""}</div></td></tr>`))}
        <div class="card-body cell-sub">Roles: <b>admin</b> everything · <b>developer</b> payments, billing, settings, webhooks · <b>support</b> refunds, disputes, verification · <b>viewer</b> read-only.</div></div>
      <div class="card" style="margin-bottom:18px"><div class="card-head"><h2>Webhook endpoints</h2><button class="btn sm" id="addEp">${I.plus} Add endpoint</button></div>
        ${eps.data.length ? eps.data.map(e => `<div class="route"><div><span class="badge b-ok">Active</span></div><div style="display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap">
          <div><div class="cell-main" style="word-break:break-all">${esc(e.url)}</div><div class="cell-sub">${esc(e.events.join(", "))}</div></div><button class="btn sm ghost" data-ep="${e.id}">${I.trash}Remove</button></div></div>`).join("")
          : `<div class="card-body cell-sub">No endpoints yet. Add one to get signed notifications for payments, invoices, subscriptions, disputes and payouts.</div>`}</div>
      <div class="split" style="grid-template-columns:1fr 1fr">
        <div class="card"><div class="card-head"><h2>Recent events</h2></div>${evs.data.length ? `<div class="list-rows">${evs.data.map(e => `<div class="lr"><span class="badge ${evBadge(e.type)}" style="text-transform:none">${esc(e.type)}</span>
          <span style="display:flex;gap:8px;align-items:center"><span class="cell-sub">${when(e.created)}</span><button class="btn sm ghost" data-resend="${e.id}">Resend</button></span></div>`).join("")}</div>` : `<div class="card-body cell-sub">No events yet.</div>`}</div>
        <div class="card"><div class="card-head"><h2>Audit log</h2></div>${logs.data.length ? `<div class="list-rows">${logs.data.map(a => `<div class="lr"><span><b>${esc(a.actor)}</b> <span class="mono">${esc(a.method)} ${esc(a.path)}</span></span>
          <span style="display:flex;gap:8px;align-items:center"><span class="badge ${a.status < 300 ? "b-ok" : a.status < 500 ? "b-warn" : "b-err"}">${a.status}</span><span class="cell-sub">${when(a.created)}</span></span></div>`).join("")}</div>` : `<div class="card-body cell-sub">Every change made with an API key is recorded here.</div>`}</div></div>`;
    $("#newKey").onclick = guard(async () => {
      const f = await modal({title: "New API key", submit: "Create key", body: field("Name", `<input name="name" required placeholder="e.g. Priya (support) or Website backend">`) +
        field("Role", `<select name="role"><option value="developer">Developer</option><option value="support">Support</option><option value="viewer">Viewer (read-only)</option><option value="admin">Admin</option></select>`)});
      if (!f) return;
      const k = await post("/v1/api_keys", {name: f.get("name"), role: f.get("role")});
      await modal({title: "Copy this key now", sub: "It's shown only once.", submit: "Done", cancel: "", body: `<div class="secret-box"><code>${esc(k.secret_key)}</code></div>`});
      reload();
    });
    bind("[data-revoke]", async d => { if (!await modal({title: "Revoke key?", sub: "Anything using it stops working immediately.", submit: "Revoke", danger: true})) return;
      await api(`/v1/api_keys/${d.revoke}`, {method: "DELETE"}); toast("Key revoked"); reload(); });
    $("#addEp").onclick = guard(async () => {
      const f = await modal({title: "Add webhook endpoint", submit: "Add endpoint", body: field("Endpoint URL", `<input name="url" type="url" required placeholder="https://yourapp.com/webhooks/sangam">`) +
        field("Events", `<select name="events"><option value="*">All events</option><option value="payment_intent.succeeded">payment_intent.succeeded only</option><option value="invoice.paid">invoice.paid only</option></select>`)});
      if (!f) return;
      const r = await post("/v1/webhook_endpoints", {url: f.get("url"), events: [f.get("events")]});
      await modal({title: "Signing secret", sub: "Verify the Sangam-Signature header with this. Shown once.", cancel: "", submit: "Done", body: `<div class="secret-box"><code>${esc(r.secret)}</code></div>`});
      reload();
    });
    bind("[data-ep]", async d => { await api(`/v1/webhook_endpoints/${d.ep}`, {method: "DELETE"}); toast("Endpoint removed"); reload(); });
    bind("[data-resend]", async d => { const r = await post(`/v1/events/${d.resend}/resend`); toast(`Queued ${r.queued_deliveries} deliver${r.queued_deliveries === 1 ? "y" : "ies"}`); });
  };

  // ---------------------------------------------------------------- Settings
  PAGES.settings = async () => {
    const m = await api("/v1/merchant"); merchant = {...merchant, ...m};
    const p = m.profile, s = m.settings;
    $("#page").innerHTML = head("Settings", "Business details for invoices, and billing behaviour.") + `
      <div class="kv">
        <div class="card"><div class="card-head"><h2>Business &amp; GST</h2></div><div class="card-body" style="display:grid;gap:14px">
          ${field("Legal name", `<input id="ln" value="${esc(p.legal_name || "")}" placeholder="${esc(m.name)}">`)}
          ${field("GSTIN", `<input id="gst" value="${esc(p.gstin || "")}" maxlength="15" style="text-transform:uppercase" placeholder="27AAPFU0939F1ZV">`, "Turns invoices into GST tax invoices with CGST/SGST or IGST.")}
          ${field("Address", `<input id="addr" value="${esc(p.address || "")}">`)}
          <div class="form-grid">${field("Support email", `<input id="se" value="${esc(p.support_email || "")}" type="email">`)}${field("Invoice prefix", `<input id="ip" value="${esc(p.invoice_prefix || "INV")}" maxlength="8" style="text-transform:uppercase">`)}</div>
          ${field("Logo URL", `<input id="logo" value="${esc(p.logo_url || "")}" placeholder="https://…/logo.png">`)}
          <div><button class="btn" id="saveProfile">Save business details</button></div></div></div>
        <div class="card"><div class="card-head"><h2>Billing</h2></div><div class="card-body" style="display:grid;gap:14px">
          ${field("Invoices due after (days)", `<input id="due" type="number" min="0" value="${s.invoice_due_days ?? 7}">`)}
          ${field("Retry failed renewals after (days)", `<input id="dun" value="${esc((s.dunning_days || [1, 3, 5]).join(", "))}">`, "Smart dunning: each number is the wait before the next retry.")}
          ${field("When all retries fail", `<select id="fin"><option value="unpaid" ${s.dunning_final_action !== "cancel" ? "selected" : ""}>Mark subscription unpaid</option><option value="cancel" ${s.dunning_final_action === "cancel" ? "selected" : ""}>Cancel subscription</option></select>`)}
          <div><button class="btn" id="saveBilling">Save billing settings</button></div></div></div></div>`;
    $("#saveProfile").onclick = guard(async () => {
      const body = {legal_name: $("#ln").value || null, address: $("#addr").value || null, support_email: $("#se").value || null, invoice_prefix: $("#ip").value.toUpperCase() || null, logo_url: $("#logo").value || null};
      if ($("#gst").value) body.gstin = $("#gst").value.toUpperCase();
      await put("/v1/merchant/profile", body); toast("Business details saved");
    });
    $("#saveBilling").onclick = guard(async () => {
      await put("/v1/merchant/settings", {invoice_due_days: +$("#due").value, dunning_days: $("#dun").value.split(",").map(x => +x.trim()).filter(Boolean), dunning_final_action: $("#fin").value});
      toast("Billing settings saved");
    });
  };

  boot();
})();
