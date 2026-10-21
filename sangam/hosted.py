"""Customer-facing hosted pages: GST invoices (/i/...), payment links (/l/...), and mandate returns."""
from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from . import billing, payments
from .billing import GST_STATES
from .db import get_db
from .models import Customer, Invoice, Merchant, PaymentIntent, PaymentLink, Plan, ProviderAccount, Subscription
from .providers import REGISTRY, ProviderError
from .routing import context_for
from .util import APIError, currency_exponent, to_major

router = APIRouter(include_in_schema=False)
e = html.escape

CSS = """
:root{--bg:#f4f3fa;--surface:#fff;--fg:#16132b;--muted:#6b6886;--line:#e7e4f3;--brand:#5b3df5;--brand-2:#0fb5a6;--ok:#0e9f6e;--ok-bg:#e6f7f0;--err:#d92d20;--err-bg:#fdecea;--warn:#b7791f;--warn-bg:#fdf3e1}
@media (prefers-color-scheme:dark){:root{--bg:#0b0a14;--surface:#15131f;--fg:#f1effa;--muted:#9a96b3;--line:#2a2740;--brand:#8b74ff;--brand-2:#2dd4bf;--ok:#34d399;--ok-bg:#0f2a22;--err:#ff8a7a;--err-bg:#2e1614;--warn:#f6c26b;--warn-bg:#2b2213}}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 "Plus Jakarta Sans",system-ui,sans-serif;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
background-image:radial-gradient(900px 500px at 0% -10%,color-mix(in srgb,var(--brand) 12%,transparent),transparent 60%)}
.wrap{max-width:820px;margin:0 auto;padding:32px 16px 60px}.card{background:var(--surface);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 40px -16px rgba(40,24,120,.18);overflow:hidden}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:inherit;font-weight:700;border:0;border-radius:12px;padding:13px 22px;
background:linear-gradient(135deg,var(--brand),color-mix(in srgb,var(--brand) 70%,var(--brand-2)));color:#fff;text-decoration:none;cursor:pointer}
.btn.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
.muted{color:var(--muted)}.badge{display:inline-block;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.b-ok{background:var(--ok-bg);color:var(--ok)}.b-err{background:var(--err-bg);color:var(--err)}.b-warn{background:var(--warn-bg);color:var(--warn)}
input{width:100%;font:inherit;padding:12px 14px;border-radius:12px;border:1.5px solid var(--line);background:var(--surface);color:inherit}
input:focus{outline:none;border-color:var(--brand)}label{font-size:13px;font-weight:600;display:block;margin-bottom:6px}
.brandmark{font-weight:800;color:var(--muted);font-size:12.5px;text-align:center;margin-top:18px}.brandmark b{color:var(--fg)}.brandmark span{color:var(--brand)}
"""
FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">')


def page(title: str, body: str, extra_css: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex"><title>{e(title)}</title>{FONT}<style>{CSS}{extra_css}</style></head>
<body>{body}</body></html>""", headers={"Cache-Control": "no-store"})


def money(amount: int, currency: str) -> str:
    major = to_major(amount, currency)
    whole, _, frac = major.partition(".")
    grouped = _group_inr(whole) if currency == "INR" else f"{int(whole):,}"
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    return f"{sym}{grouped}{'.' + frac if frac else ''}"


def _group_inr(whole: str) -> str:
    neg, whole = whole.startswith("-"), whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole


def message_page(title: str, msg: str, tone: str = "ok") -> HTMLResponse:
    icon = {"ok": "✓", "err": "✕", "warn": "…"}[tone]
    return page(title, f"""<div class="wrap" style="max-width:460px;padding-top:12vh"><div class="card" style="padding:40px 32px;text-align:center">
<div class="badge b-{tone}" style="font-size:26px;width:64px;height:64px;border-radius:50%;display:grid;place-items:center;margin:0 auto 16px">{icon}</div>
<h1 style="margin:0 0 6px;font-size:22px">{e(title)}</h1><p class="muted" style="margin:0">{e(msg)}</p></div>
<div class="brandmark">Secured by <b>Sangam<span>Pay</span></b></div></div>""")


# ---- invoice --------------------------------------------------------------------------------

INVOICE_CSS = """
.inv-head{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;padding:32px 36px;border-bottom:1px solid var(--line)}
.inv-title{font-size:28px;font-weight:800;letter-spacing:-.02em;margin:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:24px;padding:24px 36px}
.lbl{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700;margin-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:10px 36px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--line) 35%,transparent)}
td{padding:14px 36px;border-bottom:1px solid var(--line);vertical-align:top}.r{text-align:right;font-variant-numeric:tabular-nums}
.totals{padding:18px 36px;display:grid;justify-content:end}.totals div{display:flex;justify-content:space-between;gap:60px;padding:4px 0}
.grand{font-size:20px;font-weight:800;border-top:2px solid var(--fg);margin-top:8px;padding-top:10px!important}
.pay{padding:24px 36px 32px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;background:color-mix(in srgb,var(--brand) 6%,transparent)}
@media (max-width:640px){.grid2{grid-template-columns:1fr}.inv-head,.grid2,.totals,.pay{padding-left:20px;padding-right:20px}th,td{padding-left:20px;padding-right:20px}}
@media print{body{background:#fff!important;color:#000}.pay .btn,.noprint{display:none!important}.card{box-shadow:none;border:0}.wrap{padding:0}}
"""


@router.get("/i/{token}")
def invoice_page(token: str, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter_by(access_token=token).first()
    if inv is None or inv.status == "draft":
        return message_page("Invoice not found", "This invoice link is invalid or not issued yet.", "err")
    m = db.get(Merchant, inv.merchant_id)
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    p = m.profile or {}
    status = {"open": ("b-warn", "Due"), "paid": ("b-ok", "Paid"), "void": ("b-err", "Void"),
              "uncollectible": ("b-err", "Uncollectible")}[inv.status]
    gst_title = "Tax Invoice" if p.get("gstin") else "Invoice"
    rows = "".join(f"""<tr><td><div style="font-weight:600">{e(li['description'])}</div>
        {f'<div class="muted" style="font-size:12.5px">HSN/SAC {e(li["hsn_sac"])}</div>' if li.get('hsn_sac') else ''}</td>
        <td class="r">{li['quantity']}</td><td class="r">{money(li['unit_amount'], inv.currency)}</td>
        <td class="r">{li['tax_rate']:g}%</td><td class="r">{money(li['amount'], inv.currency)}</td></tr>""" for li in inv.line_items)
    taxes = "".join(f"<div><span class='muted'>{e(t['name'])}</span><span class='r'>{money(t['amount'], inv.currency)}</span></div>"
                    for t in inv.tax_breakdown)
    seller_state = GST_STATES.get(p.get("state_code") or "", "")
    buyer_state = GST_STATES.get((c.state_code if c else None) or "", "")
    pay_block = ""
    if inv.status == "open" and inv.payment_intent_id:
        intent = db.get(PaymentIntent, inv.payment_intent_id)
        pay_block = f"""<div class="pay"><div><div class="lbl">Amount due</div><div style="font-size:24px;font-weight:800">{money(inv.total, inv.currency)}</div>
            <div class="muted" style="font-size:13px">Due {inv.due_date:%d %b %Y}</div></div>
            <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost noprint" onclick="print()">Download PDF</button>
            <a class="btn" href="{e(payments.checkout_url(intent))}">Pay {money(inv.total, inv.currency)}</a></div></div>"""
    elif inv.status == "paid":
        pay_block = f"""<div class="pay"><div><span class="badge b-ok">Paid</span> <span class="muted">on {inv.paid_at:%d %b %Y}</span></div>
            <button class="btn ghost noprint" onclick="print()">Download PDF</button></div>"""
    body = f"""<div class="wrap"><div class="card">
<div class="inv-head"><div>{f'<img src="{e(p["logo_url"])}" alt="" style="height:40px;margin-bottom:12px">' if p.get('logo_url') else ''}
  <p class="inv-title">{gst_title}</p><div class="muted">{e(inv.number or '')}</div></div>
  <div style="text-align:right"><span class="badge {status[0]}">{status[1]}</span>
  <div class="muted" style="margin-top:10px;font-size:13.5px">Issued {inv.created_at:%d %b %Y}</div>
  {f'<div class="muted" style="font-size:13.5px">Period {inv.period_start:%d %b %Y} – {inv.period_end:%d %b %Y}</div>' if inv.period_start else ''}</div></div>
<div class="grid2"><div><div class="lbl">From</div><div style="font-weight:700">{e(p.get('legal_name') or m.name)}</div>
  <div class="muted" style="white-space:pre-line;font-size:13.5px">{e(p.get('address') or '')}</div>
  {f'<div style="font-size:13.5px">GSTIN <b>{e(p["gstin"])}</b></div>' if p.get('gstin') else ''}
  {f'<div class="muted" style="font-size:13.5px">State: {e(seller_state)} ({e(p.get("state_code"))})</div>' if seller_state else ''}
  {f'<div class="muted" style="font-size:13.5px">{e(p["support_email"])}</div>' if p.get('support_email') else ''}</div>
 <div><div class="lbl">Bill to</div>{f'''<div style="font-weight:700">{e(c.name or c.email or '')}</div>
  <div class="muted" style="font-size:13.5px">{e(c.email or '')}</div><div class="muted" style="white-space:pre-line;font-size:13.5px">{e(c.address or '')}</div>
  {f'<div style="font-size:13.5px">GSTIN <b>{e(c.gstin)}</b></div>' if c.gstin else ''}
  {f'<div class="muted" style="font-size:13.5px">Place of supply: {e(buyer_state)} ({e(c.state_code)})</div>' if buyer_state else ''}''' if c else '<div class="muted">—</div>'}</div></div>
<div style="overflow-x:auto"><table><thead><tr><th>Item</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Tax</th><th class="r">Amount</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="totals"><div><span class="muted">Subtotal</span><span class="r">{money(inv.subtotal, inv.currency)}</span></div>{taxes}
  <div class="grand"><span>Total</span><span class="r">{money(inv.total, inv.currency)}</span></div></div>
{f'<div style="padding:0 36px 20px" class="muted"><div class="lbl">Notes</div><div style="white-space:pre-line">{e(inv.notes)}</div></div>' if inv.notes else ''}
{pay_block}</div><div class="brandmark noprint">Powered by <b>Sangam<span>Pay</span></b></div></div>"""
    return page(f"{gst_title} {inv.number} · {m.name}", body, INVOICE_CSS)


# ---- payment links ----------------------------------------------------------------------------

LINK_CSS = """
.hero{padding:36px 32px 28px;color:#fff;background:linear-gradient(150deg,#2a1a8a,#5b3df5 55%,#0fb5a6 140%)}
.hero h1{margin:8px 0 4px;font-size:26px;letter-spacing:-.02em}.amt{font-size:40px;font-weight:800;letter-spacing:-.03em;margin-top:14px}
.form{padding:28px 32px 32px;display:grid;gap:16px}
"""


@router.get("/l/{slug}")
def link_page(slug: str, db: Session = Depends(get_db), error: str | None = None):
    link = db.query(PaymentLink).filter_by(slug=slug).first()
    if link is None:
        return message_page("Link not found", "Check the link with the person who shared it.", "err")
    m = db.get(Merchant, link.merchant_id)
    if not link.active:
        return message_page("This link is closed", f"{m.name} is no longer accepting payments on this link.", "warn")
    exp = currency_exponent(link.currency)
    amount_field = "" if link.amount else f"""<div><label for="amount">Amount ({e(link.currency)})</label>
        <input id="amount" name="amount" type="number" inputmode="decimal" required step="{1 / 10 ** exp if exp else 1}"
        min="{to_major(link.min_amount, link.currency) if link.min_amount else (1 / 10 ** exp if exp else 1)}" placeholder="0.00"></div>"""
    body = f"""<div class="wrap" style="max-width:480px;padding-top:6vh"><div class="card">
<div class="hero"><div style="opacity:.85;font-weight:600">{e(m.name)}{'' if link.mode == 'live' else ' · <span style="background:#ffd166;color:#4a3300;padding:2px 8px;border-radius:999px;font-size:11px">TEST MODE</span>'}</div>
<h1>{e(link.title)}</h1>{f'<div style="opacity:.85">{e(link.description)}</div>' if link.description else ''}
{f'<div class="amt">{money(link.amount, link.currency)}</div>' if link.amount else ''}</div>
<form class="form" method="post">{amount_field}
<div><label for="email">Email for receipt <span class="muted">(optional)</span></label><input id="email" name="email" type="email" autocomplete="email"></div>
{f'<div class="badge b-err" style="text-transform:none;letter-spacing:0">{e(error)}</div>' if error else ''}
<button class="btn" type="submit">Continue to payment →</button></form></div>
<div class="brandmark">Secured by <b>Sangam<span>Pay</span></b></div></div>"""
    return page(f"{link.title} · {m.name}", body, LINK_CSS)


@router.post("/l/{slug}")
def link_submit(slug: str, amount: str | None = Form(default=None), email: str | None = Form(default=None),
                db: Session = Depends(get_db)):
    link = db.query(PaymentLink).filter_by(slug=slug).first()
    if link is None:
        return message_page("Link not found", "Check the link with the person who shared it.", "err")
    minor = None
    if link.amount is None:
        try:
            minor = int((Decimal(amount or "0") * 10 ** currency_exponent(link.currency)).to_integral_value())
        except InvalidOperation:
            minor = 0
    try:
        intent = billing.link_checkout(db, link, minor, (email or "").strip() or None)
    except APIError as exc:
        return link_page(slug, db, error=exc.message)
    return RedirectResponse(payments.checkout_url(intent), status_code=303)


# ---- subscription mandates --------------------------------------------------------------------

@router.get("/subscribe/{sub_id}/return")
def mandate_return(sub_id: str, canceled: int = 0, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None:
        return message_page("Subscription not found", "This link is invalid.", "err")
    account = db.get(ProviderAccount, sub.mandate_account_id) if sub.mandate_account_id else None
    try:
        status = REGISTRY[sub.mandate_provider].sync_mandate(context_for(account, sub.mode), sub,
                                                              {"canceled": bool(canceled), **(sub.metadata_ or {}).get("_sandbox", {})})
        billing.mandate_authorized(db, sub, status)
        db.commit()
    except ProviderError:
        pass  # the gateway webhook will settle it
    m = db.get(Merchant, sub.merchant_id)
    if sub.mandate_status == "active":
        return message_page("AutoPay set up", f"You're subscribed to {m.name}. Payments will be collected automatically; "
                                              "you can cancel anytime from your UPI app or bank.", "ok")
    if sub.mandate_status == "revoked":
        return message_page("Mandate not set up", "No payment was taken. You can try again from the merchant's site.", "err")
    return message_page("Waiting for your bank", "Your mandate is being confirmed. You'll get a notification shortly.", "warn")


@router.get("/sandbox/mandate/{sub_id}")
def sandbox_mandate(sub_id: str, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.mandate_provider != "mock":
        return message_page("Not found", "Sandbox mandate not found.", "err")
    plan = db.get(Plan, sub.plan_id)
    every = f"every {plan.interval_count} {plan.interval}s" if plan.interval_count > 1 else f"every {plan.interval}"
    label = {"upi": "UPI AutoPay", "emandate": "e-NACH mandate", "card": "Card e-mandate", "bank_debit": "Bank debit"}
    body = f"""<div class="wrap" style="max-width:440px;padding-top:10vh"><div class="card" style="padding:32px;text-align:center;border:2px dashed #d9a300">
<div class="badge b-warn">Test mode · Sandbox bank</div><h1 style="font-size:22px;margin:14px 0 4px">Authorise {e(label.get(sub.mandate_method, 'mandate'))}</h1>
<p class="muted">{e(plan.name)}: <b style="color:var(--fg)">{money(plan.amount * sub.quantity, plan.currency)}</b> {every}</p>
<form method="post" action="/sandbox/mandate/{e(sub.id)}/approve"><button class="btn" style="width:100%;margin-top:8px">Approve mandate</button></form>
<form method="post" action="/sandbox/mandate/{e(sub.id)}/decline"><button class="btn ghost" style="width:100%;margin-top:8px">Decline</button></form></div></div>"""
    return page("Sandbox mandate", body)


@router.post("/sandbox/mandate/{sub_id}/{decision}")
def sandbox_mandate_decide(sub_id: str, decision: str, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.mandate_provider != "mock" or decision not in ("approve", "decline"):
        return message_page("Not found", "Sandbox mandate not found.", "err")
    sub.metadata_ = {**(sub.metadata_ or {}), "_sandbox": {"decision": decision}}
    db.commit()
    return RedirectResponse(f"/subscribe/{sub.id}/return", status_code=303)
