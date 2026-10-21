"""Billing engine: GST-ready invoices, subscriptions with mandates (UPI AutoPay, e-NACH, cards,
ACH/SEPA) or invoice-per-cycle collection, smart dunning, and payment links.

Subscriptions with a gateway that runs its own schedule (Razorpay, Stripe) are mirrored from
webhooks. Everything else (sandbox mandates, invoice collection, which works with zero-fee UPI)
is scheduled here by `run_billing`, which the background worker calls every 30 seconds."""
from __future__ import annotations

import calendar
import secrets
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import payments
from .config import settings
from .models import (Customer, Invoice, Merchant, PaymentAttempt, PaymentIntent, PaymentLink, Plan,
                     ProviderAccount, Subscription)
from .providers import REGISTRY, ProviderError
from .routing import context_for
from .util import APIError, iso, new_id, utcnow
from .webhooks import emit

DEFAULT_DUNNING_DAYS = [1, 3, 5]  # retry a failed renewal after 1, 3 and 5 more days
DEFAULT_DUE_DAYS = 7

# GST state codes (first two digits of a GSTIN).
GST_STATES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh",
    "23": "Madhya Pradesh", "24": "Gujarat", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}


# ---- time helpers -------------------------------------------------------------------------

def add_interval(dt: datetime, interval: str, count: int = 1) -> datetime:
    if interval == "day":
        return dt + timedelta(days=count)
    if interval == "week":
        return dt + timedelta(weeks=count)
    months = count * (12 if interval == "year" else 1)
    month0 = dt.month - 1 + months
    year, month = dt.year + month0 // 12, month0 % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])  # Jan 31 + 1 month = Feb 28/29
    return dt.replace(year=year, month=month, day=day)


def monthly_amount(plan: Plan, quantity: int = 1) -> float:
    per = plan.amount * quantity / max(plan.interval_count, 1)
    return {"day": per * 30, "week": per * 52 / 12, "month": per, "year": per / 12}[plan.interval]


# ---- serializers ----------------------------------------------------------------------------

def serialize_customer(c: Customer) -> dict:
    return {"id": c.id, "object": "customer", "email": c.email, "name": c.name, "phone": c.phone,
            "gstin": c.gstin, "state_code": c.state_code, "address": c.address, "metadata": c.metadata_,
            "created": iso(c.created_at)}


def serialize_plan(p: Plan) -> dict:
    return {"id": p.id, "object": "plan", "name": p.name, "amount": p.amount, "currency": p.currency,
            "interval": p.interval, "interval_count": p.interval_count, "trial_days": p.trial_days,
            "tax_rate": p.tax_rate, "hsn_sac": p.hsn_sac, "active": p.active, "created": iso(p.created_at)}


def hosted_invoice_url(inv: Invoice) -> str:
    return f"{settings.base_url}/i/{inv.access_token}"


def serialize_invoice(inv: Invoice) -> dict:
    return {"id": inv.id, "object": "invoice", "number": inv.number, "status": inv.status,
            "customer": inv.customer_id, "subscription": inv.subscription_id, "currency": inv.currency,
            "line_items": inv.line_items, "subtotal": inv.subtotal, "tax_total": inv.tax_total, "total": inv.total,
            "tax_breakdown": inv.tax_breakdown, "period_start": iso(inv.period_start), "period_end": iso(inv.period_end),
            "due_date": iso(inv.due_date), "paid_at": iso(inv.paid_at), "payment_intent": inv.payment_intent_id,
            "hosted_invoice_url": hosted_invoice_url(inv), "notes": inv.notes, "created": iso(inv.created_at)}


def serialize_subscription(s: Subscription) -> dict:
    return {"id": s.id, "object": "subscription", "status": s.status, "customer": s.customer_id, "plan": s.plan_id,
            "quantity": s.quantity, "collection": s.collection, "mandate": {
                "method": s.mandate_method, "provider": s.mandate_provider, "status": s.mandate_status,
                "authorization_url": s.authorization_url} if s.collection == "mandate" else None,
            "current_period_start": iso(s.current_period_start), "current_period_end": iso(s.current_period_end),
            "trial_end": iso(s.trial_end), "cancel_at_period_end": s.cancel_at_period_end,
            "canceled_at": iso(s.canceled_at), "retry_count": s.retry_count, "next_retry_at": iso(s.next_retry_at),
            "metadata": {k: v for k, v in (s.metadata_ or {}).items() if not k.startswith("_")},
            "created": iso(s.created_at)}


def link_url(link: PaymentLink) -> str:
    return f"{settings.base_url}/l/{link.slug}"


def serialize_link(link: PaymentLink) -> dict:
    return {"id": link.id, "object": "payment_link", "url": link_url(link), "title": link.title,
            "description": link.description, "amount": link.amount, "min_amount": link.min_amount,
            "currency": link.currency, "payment_methods": link.payment_methods, "max_payments": link.max_payments,
            "payments_count": link.payments_count, "amount_collected": link.amount_collected,
            "active": link.active, "expires_at": iso(link.expires_at), "created": iso(link.created_at)}


# ---- invoices -------------------------------------------------------------------------------

def compute_totals(merchant: Merchant, customer: Customer | None, items: list[dict]) -> tuple[list, int, int, list]:
    """GST: intra-state supply -> CGST + SGST (half each); inter-state -> IGST. Without a merchant
    GSTIN, any tax_rate is shown as plain 'Tax'."""
    profile = merchant.profile or {}
    gst = bool(profile.get("gstin"))
    seller_state = profile.get("state_code") or (profile.get("gstin") or "")[:2] or None
    buyer_state = (customer.state_code or (customer.gstin or "")[:2] or None) if customer else None
    intra = buyer_state is None or buyer_state == seller_state

    lines, subtotal, by_rate = [], 0, defaultdict(int)
    for it in items:
        qty = int(it.get("quantity", 1))
        unit = int(it["unit_amount"])
        if qty <= 0 or unit < 0:
            raise APIError(400, "invalid_line_item", "quantity must be > 0 and unit_amount >= 0", "line_items")
        rate = float(it.get("tax_rate") or 0)
        amount = qty * unit
        tax = round(amount * rate / 100)
        subtotal += amount
        by_rate[rate] += tax
        lines.append({"description": str(it["description"])[:300], "quantity": qty, "unit_amount": unit,
                      "amount": amount, "tax_rate": rate, "tax": tax, "hsn_sac": it.get("hsn_sac")})

    breakdown = []
    for rate, tax in sorted(by_rate.items()):
        if not tax:
            continue
        r = f"{rate:g}"
        if gst and intra:
            half = tax // 2
            breakdown += [{"name": f"CGST {float(rate) / 2:g}%", "amount": half},
                          {"name": f"SGST {float(rate) / 2:g}%", "amount": tax - half}]
        elif gst:
            breakdown.append({"name": f"IGST {r}%", "amount": tax})
        else:
            breakdown.append({"name": f"Tax {r}%", "amount": tax})
    tax_total = sum(b["amount"] for b in breakdown)
    return lines, subtotal, tax_total, breakdown


def _next_number(db: Session, merchant: Merchant, mode: str) -> str:
    prefix = (merchant.profile or {}).get("invoice_prefix") or "INV"
    year = utcnow().year
    count = db.query(Invoice).filter(Invoice.merchant_id == merchant.id, Invoice.mode == mode,
                                     Invoice.number.like(f"{prefix}-{year}-%")).count()
    return f"{prefix}-{year}-{count + 1:05d}"


def create_invoice(db: Session, merchant: Merchant, mode: str, *, customer: Customer | None, items: list[dict],
                   currency: str, subscription: Subscription | None = None, period: tuple | None = None,
                   due_days: int | None = None, notes: str | None = None, finalize: bool = True) -> Invoice:
    if not items:
        raise APIError(400, "missing_field", "An invoice needs at least one line item", "line_items")
    lines, subtotal, tax_total, breakdown = compute_totals(merchant, customer, items)
    if due_days is None:
        due_days = int((merchant.settings or {}).get("invoice_due_days", DEFAULT_DUE_DAYS))
    inv = Invoice(id=new_id("in"), merchant_id=merchant.id, mode=mode, access_token=secrets.token_urlsafe(24),
                  customer_id=customer.id if customer else None, subscription_id=subscription.id if subscription else None,
                  status="draft", currency=currency.upper(), line_items=lines, subtotal=subtotal, tax_total=tax_total,
                  total=subtotal + tax_total, tax_breakdown=breakdown, notes=notes,
                  period_start=period[0] if period else None, period_end=period[1] if period else None,
                  due_date=utcnow() + timedelta(days=due_days))
    db.add(inv)
    db.flush()
    if finalize:
        finalize_invoice(db, merchant, inv)
    return inv


def finalize_invoice(db: Session, merchant: Merchant, inv: Invoice) -> Invoice:
    if inv.status != "draft":
        raise APIError(409, "invalid_state", "Only draft invoices can be finalized")
    customer = db.get(Customer, inv.customer_id) if inv.customer_id else None
    inv.number = _next_number(db, merchant, inv.mode)
    if inv.total == 0:
        inv.status, inv.paid_at = "paid", utcnow()
        emit(db, merchant.id, inv.mode, "invoice.paid", serialize_invoice(inv))
        return inv
    intent = payments.create_intent(
        db, merchant.id, inv.mode, amount=inv.total, currency=inv.currency,
        description=f"Invoice {inv.number}", customer=_customer_dict(customer),
        customer_id=inv.customer_id, invoice_id=inv.id, subscription_id=inv.subscription_id)
    inv.payment_intent_id, inv.status = intent.id, "open"
    emit(db, merchant.id, inv.mode, "invoice.finalized", serialize_invoice(inv))
    return inv


def void_invoice(db: Session, inv: Invoice) -> Invoice:
    if inv.status not in ("draft", "open"):
        raise APIError(409, "invalid_state", "Only draft or open invoices can be voided")
    inv.status = "void"
    if inv.payment_intent_id:
        intent = db.get(PaymentIntent, inv.payment_intent_id)
        if intent and intent.status in payments.OPEN:
            intent.status = "canceled"
    emit(db, inv.merchant_id, inv.mode, "invoice.voided", serialize_invoice(inv))
    return inv


def mark_paid_out_of_band(db: Session, inv: Invoice, note: str | None) -> Invoice:
    """Cash, cheque or bank transfer received outside SangamPay."""
    if inv.status != "open":
        raise APIError(409, "invalid_state", "Only open invoices can be marked paid")
    inv.status, inv.paid_at = "paid", utcnow()
    inv.notes = ((inv.notes or "") + f"\nPaid out of band: {note or ''}").strip()
    if inv.payment_intent_id:
        intent = db.get(PaymentIntent, inv.payment_intent_id)
        if intent and intent.status in payments.OPEN:
            intent.status = "canceled"
    emit(db, inv.merchant_id, inv.mode, "invoice.paid", serialize_invoice(inv))
    _subscription_paid(db, inv)
    return inv


def _customer_dict(c: Customer | None) -> dict:
    if not c:
        return {}
    return {k: v for k, v in {"email": c.email, "name": c.name, "phone": c.phone}.items() if v}


# ---- hooks from the payment lifecycle ------------------------------------------------------

def on_payment_succeeded(db: Session, intent: PaymentIntent) -> None:
    if intent.invoice_id:
        inv = db.get(Invoice, intent.invoice_id)
        if inv and inv.status in ("open", "draft", "void"):
            inv.status, inv.paid_at = "paid", utcnow()
            emit(db, inv.merchant_id, inv.mode, "invoice.paid", serialize_invoice(inv))
            _subscription_paid(db, inv)
    if intent.payment_link_id:
        link = db.get(PaymentLink, intent.payment_link_id)
        if link:
            link.payments_count += 1
            link.amount_collected += intent.amount
            if link.max_payments and link.payments_count >= link.max_payments:
                link.active = False


def on_payment_failed(db: Session, intent: PaymentIntent) -> None:
    pass  # invoice-collection customers simply retry; mandate failures are handled in _charge


def _subscription_paid(db: Session, inv: Invoice) -> None:
    if not inv.subscription_id:
        return
    sub = db.get(Subscription, inv.subscription_id)
    if sub and sub.status in ("incomplete", "past_due", "unpaid"):
        sub.status, sub.retry_count, sub.next_retry_at = "active", 0, None
        emit(db, sub.merchant_id, sub.mode, "subscription.activated", serialize_subscription(sub))


# ---- subscriptions ---------------------------------------------------------------------------

def _mandate_route(db: Session, merchant: Merchant, mode: str, method: str, currency: str):
    accounts = {a.provider: a for a in db.query(ProviderAccount).filter_by(merchant_id=merchant.id, mode=mode,
                                                                          enabled=True)}
    order = ["razorpay", "stripe"] + (["mock"] if mode == "test" else [])
    for name in order:
        p = REGISTRY[name]
        if method not in p.mandate_methods or (p.currencies and currency not in p.currencies):
            continue
        if p.needs_account and name not in accounts:
            continue
        return p, accounts.get(name)
    raise APIError(400, "mandate_unavailable",
                   f"No connected gateway can set up a {method} mandate in {currency}. Connect Razorpay (UPI AutoPay, "
                   f"e-NACH, cards) or Stripe (cards, ACH/SEPA).", "mandate_method")


def create_subscription(db: Session, merchant: Merchant, mode: str, *, customer: Customer, plan: Plan,
                        collection: str, mandate_method: str | None, trial_days: int | None, quantity: int,
                        metadata: dict) -> Subscription:
    if not plan.active:
        raise APIError(400, "plan_inactive", "This plan is archived", "plan")
    now = utcnow()
    trial = plan.trial_days if trial_days is None else trial_days
    sub = Subscription(id=new_id("sub"), merchant_id=merchant.id, mode=mode, customer_id=customer.id, plan_id=plan.id,
                       quantity=quantity, collection=collection, metadata_=metadata, status="incomplete",
                       current_period_start=now)
    if trial:
        sub.trial_end = now + timedelta(days=trial)
        sub.current_period_end = sub.trial_end
    else:
        sub.current_period_end = add_interval(now, plan.interval, plan.interval_count)
    db.add(sub)
    db.flush()

    if collection == "invoice":
        if trial:
            sub.status = "trialing"
        else:
            _invoice_for_period(db, merchant, sub, plan, customer, (sub.current_period_start, sub.current_period_end))
    else:
        if not mandate_method:
            raise APIError(400, "missing_field", "mandate_method is required for mandate collection", "mandate_method")
        provider, account = _mandate_route(db, merchant, mode, mandate_method, plan.currency)
        sub.mandate_method, sub.mandate_provider = mandate_method, provider.name
        sub.mandate_account_id = account.id if account else None
        try:
            res = provider.create_mandate(context_for(account, mode), sub, plan, customer,
                                          f"{settings.base_url}/subscribe/{sub.id}/return")
        except ProviderError as exc:
            raise APIError(502, "provider_error", str(exc)) from exc
        sub.mandate_ref, sub.authorization_url, sub.mandate_status = res.provider_ref, res.authorization_url, "pending"
    emit(db, merchant.id, mode, "subscription.created", serialize_subscription(sub))
    return sub


def _invoice_for_period(db: Session, merchant: Merchant, sub: Subscription, plan: Plan, customer: Customer,
                        period: tuple) -> Invoice:
    label = f"{plan.name} ({period[0]:%d %b %Y} – {period[1]:%d %b %Y})"
    return create_invoice(db, merchant, sub.mode, customer=customer, currency=plan.currency, subscription=sub,
                          period=period, items=[{"description": label, "quantity": sub.quantity,
                                                 "unit_amount": plan.amount, "tax_rate": plan.tax_rate,
                                                 "hsn_sac": plan.hsn_sac}])


def mandate_authorized(db: Session, sub: Subscription, status: str) -> None:
    """Called when the customer returns from the mandate page, or by gateway webhook."""
    if status == sub.mandate_status:
        return
    sub.mandate_status = status
    if status == "revoked":
        if sub.status != "canceled":
            sub.status, sub.canceled_at = "canceled", utcnow()
            emit(db, sub.merchant_id, sub.mode, "subscription.canceled", serialize_subscription(sub))
        return
    if status != "active":
        return
    emit(db, sub.merchant_id, sub.mode, "subscription.mandate_authorized", serialize_subscription(sub))
    if sub.trial_end and sub.trial_end > utcnow():
        sub.status = "trialing"
        return
    if REGISTRY[sub.mandate_provider].managed_billing:
        sub.status = "active"  # the gateway takes the first charge and reports it by webhook
        return
    merchant, plan = db.get(Merchant, sub.merchant_id), db.get(Plan, sub.plan_id)
    inv = _invoice_for_period(db, merchant, sub, plan, db.get(Customer, sub.customer_id),
                              (sub.current_period_start, sub.current_period_end))
    _charge(db, sub, inv)


def _charge(db: Session, sub: Subscription, inv: Invoice) -> bool:
    """Debit a sandbox/self-scheduled mandate for an open invoice. Returns True when paid."""
    intent = db.get(PaymentIntent, inv.payment_intent_id)
    provider = REGISTRY[sub.mandate_provider]
    account = db.get(ProviderAccount, sub.mandate_account_id) if sub.mandate_account_id else None
    attempt = PaymentAttempt(id=new_id("pa"), intent_id=intent.id, provider=provider.name,
                             provider_account_id=sub.mandate_account_id, method=sub.mandate_method, status="created",
                             data={"mandate": sub.mandate_ref})
    db.add(attempt)
    intent.latest_attempt_id, intent.provider, intent.method = attempt.id, provider.name, sub.mandate_method
    intent.status = "requires_action"
    db.flush()
    inv.attempt_count += 1
    try:
        status, ref = provider.charge_mandate(context_for(account, sub.mode), sub, intent)
    except ProviderError as exc:
        status, ref = "failed", None
        attempt.error = str(exc)[:500]
    attempt.provider_ref = ref
    payments.apply_status(db, intent, attempt, status, failure_reason="Mandate debit failed" if status == "failed" else None)
    if status == "succeeded":
        return True
    _schedule_retry(db, sub)
    return False


def _schedule_retry(db: Session, sub: Subscription) -> None:
    merchant = db.get(Merchant, sub.merchant_id)
    days = (merchant.settings or {}).get("dunning_days") or DEFAULT_DUNNING_DAYS
    sub.retry_count += 1
    if sub.retry_count > len(days):
        final = (merchant.settings or {}).get("dunning_final_action", "unpaid")  # unpaid | cancel
        sub.next_retry_at = None
        if final == "cancel":
            sub.status, sub.canceled_at = "canceled", utcnow()
            emit(db, sub.merchant_id, sub.mode, "subscription.canceled", serialize_subscription(sub))
        else:
            sub.status = "unpaid"
            emit(db, sub.merchant_id, sub.mode, "subscription.unpaid", serialize_subscription(sub))
        return
    sub.status = "past_due"
    sub.next_retry_at = utcnow() + timedelta(days=days[sub.retry_count - 1])
    emit(db, sub.merchant_id, sub.mode, "subscription.payment_failed",
         {**serialize_subscription(sub), "attempt": sub.retry_count})


def record_managed_charge(db: Session, sub: Subscription, amount: int | None, payment_id: str | None) -> None:
    """Razorpay/Stripe charged a mandate: mirror it as a paid invoice and advance the period."""
    if payment_id and db.query(PaymentAttempt).filter_by(provider_payment_id=payment_id).first():
        return  # duplicate webhook
    merchant, plan = db.get(Merchant, sub.merchant_id), db.get(Plan, sub.plan_id)
    now = utcnow()
    period = (now, add_interval(now, plan.interval, plan.interval_count))
    inv = _invoice_for_period(db, merchant, sub, plan, db.get(Customer, sub.customer_id), period)
    intent = db.get(PaymentIntent, inv.payment_intent_id)
    attempt = PaymentAttempt(id=new_id("pa"), intent_id=intent.id, provider=sub.mandate_provider,
                             provider_account_id=sub.mandate_account_id, method=sub.mandate_method or "card",
                             status="created", provider_payment_id=payment_id, data={"mandate": sub.mandate_ref})
    db.add(attempt)
    intent.latest_attempt_id = attempt.id
    db.flush()
    payments.apply_status(db, intent, attempt, "succeeded")
    sub.current_period_start, sub.current_period_end = period
    sub.status, sub.mandate_status, sub.retry_count = "active", "active", 0


def record_managed_failure(db: Session, sub: Subscription) -> None:
    if sub.status != "past_due":
        sub.status = "past_due"
        emit(db, sub.merchant_id, sub.mode, "subscription.payment_failed", serialize_subscription(sub))


def cancel_subscription(db: Session, sub: Subscription, at_period_end: bool) -> Subscription:
    if sub.status == "canceled":
        raise APIError(409, "invalid_state", "Subscription is already canceled")
    if at_period_end:
        sub.cancel_at_period_end = True
        emit(db, sub.merchant_id, sub.mode, "subscription.updated", serialize_subscription(sub))
        return sub
    if sub.mandate_provider and sub.mandate_status != "revoked":
        account = db.get(ProviderAccount, sub.mandate_account_id) if sub.mandate_account_id else None
        try:
            REGISTRY[sub.mandate_provider].cancel_mandate(context_for(account, sub.mode), sub)
        except ProviderError as exc:
            raise APIError(502, "provider_error", f"Could not cancel the mandate at the gateway: {exc}") from exc
        sub.mandate_status = "revoked"
    sub.status, sub.canceled_at = "canceled", utcnow()
    for inv in db.query(Invoice).filter_by(subscription_id=sub.id, status="open"):
        void_invoice(db, inv)
    emit(db, sub.merchant_id, sub.mode, "subscription.canceled", serialize_subscription(sub))
    return sub


def run_billing(db: Session, now: datetime | None = None) -> dict:
    """Renewals, invoice-collection past-due handling and dunning retries. Safe to run often."""
    now = now or utcnow()
    summary = {"renewed": 0, "charged": 0, "failed": 0, "past_due": 0, "canceled": 0}

    due = (db.query(Subscription).filter(Subscription.status.in_(("trialing", "active", "past_due")),
                                         Subscription.current_period_end <= now).all())
    for sub in due:
        if sub.collection == "mandate" and REGISTRY[sub.mandate_provider].managed_billing:
            continue  # the gateway runs the schedule
        if sub.collection == "mandate" and sub.mandate_status != "active":
            continue
        if sub.cancel_at_period_end:
            sub.status, sub.canceled_at = "canceled", now
            emit(db, sub.merchant_id, sub.mode, "subscription.canceled", serialize_subscription(sub))
            summary["canceled"] += 1
            continue
        merchant, plan = db.get(Merchant, sub.merchant_id), db.get(Plan, sub.plan_id)
        start = sub.current_period_end
        period = (start, add_interval(start, plan.interval, plan.interval_count))
        sub.current_period_start, sub.current_period_end = period
        if sub.status == "trialing":
            sub.status = "active"
        inv = _invoice_for_period(db, merchant, sub, plan, db.get(Customer, sub.customer_id), period)
        summary["renewed"] += 1
        if sub.collection == "mandate":
            if _charge(db, sub, inv):
                summary["charged"] += 1
            else:
                summary["failed"] += 1
        db.commit()

    # Invoice collection: unpaid past the due date -> past_due, then unpaid after the grace period.
    overdue = (db.query(Invoice, Subscription).join(Subscription, Subscription.id == Invoice.subscription_id)
               .filter(Invoice.status == "open", Invoice.due_date < now, Subscription.collection == "invoice",
                       Subscription.status.in_(("active", "incomplete", "past_due"))).all())
    for inv, sub in overdue:
        merchant = db.get(Merchant, sub.merchant_id)
        grace = sum((merchant.settings or {}).get("dunning_days") or DEFAULT_DUNNING_DAYS)
        if inv.due_date + timedelta(days=grace) < now:
            sub.status = "unpaid"
            emit(db, sub.merchant_id, sub.mode, "subscription.unpaid", serialize_subscription(sub))
        elif sub.status != "past_due":
            sub.status = "past_due"
            emit(db, sub.merchant_id, sub.mode, "subscription.past_due", serialize_subscription(sub))
            summary["past_due"] += 1
    db.commit()

    # Dunning: retry mandate debits on schedule.
    retries = (db.query(Subscription).filter(Subscription.status == "past_due", Subscription.collection == "mandate",
                                             Subscription.next_retry_at <= now).all())
    for sub in retries:
        inv = (db.query(Invoice).filter_by(subscription_id=sub.id, status="open")
               .order_by(Invoice.created_at.desc()).first())
        if inv is None:
            sub.status, sub.next_retry_at = "active", None
            continue
        intent = db.get(PaymentIntent, inv.payment_intent_id)
        if intent.status not in payments.OPEN:
            intent.status = "requires_payment_method"
        if _charge(db, sub, inv):
            summary["charged"] += 1
        else:
            summary["failed"] += 1
        db.commit()
    return summary


def advance_clock(db: Session, sub: Subscription, days: float) -> dict:
    """Test-mode time travel: pretend `days` have passed for this subscription, then run billing."""
    if sub.mode != "test":
        raise APIError(400, "test_mode_only", "Time travel is only available in test mode")
    delta = timedelta(days=days)
    for field in ("current_period_start", "current_period_end", "trial_end", "next_retry_at", "created_at"):
        if getattr(sub, field):
            setattr(sub, field, getattr(sub, field) - delta)
    for inv in db.query(Invoice).filter_by(subscription_id=sub.id):
        for field in ("due_date", "period_start", "period_end", "created_at"):
            if getattr(inv, field):
                setattr(inv, field, getattr(inv, field) - delta)
    db.commit()
    return run_billing(db)


# ---- payment links --------------------------------------------------------------------------

def link_checkout(db: Session, link: PaymentLink, amount: int | None, email: str | None) -> PaymentIntent:
    now = utcnow()
    if not link.active or (link.expires_at and link.expires_at < now):
        raise APIError(410, "link_inactive", "This payment link is no longer active")
    if link.amount is None:
        if not amount or amount <= 0:
            raise APIError(400, "invalid_amount", "Enter an amount", "amount")
        if link.min_amount and amount < link.min_amount:
            raise APIError(400, "invalid_amount", "Amount is below the minimum for this link", "amount")
    customer = {"email": email} if email else {}
    intent = payments.create_intent(db, link.merchant_id, link.mode, amount=link.amount or amount,
                                    currency=link.currency, description=link.title, customer=customer,
                                    payment_methods=link.payment_methods, payment_link_id=link.id)
    db.commit()
    return intent
