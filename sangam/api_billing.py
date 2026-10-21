"""Billing API: customers, plans, subscriptions, GST invoices, payment links."""
from __future__ import annotations

import secrets
import string
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import billing
from .db import get_db
from .models import Customer, Invoice, Merchant, PaymentLink, Plan, Subscription
from .providers import MANDATE_METHODS, METHODS
from .security import Auth, require_secret_key, scoped
from .util import APIError, new_id

router = APIRouter(prefix="/v1")
GSTIN = r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"


def _own(db: Session, model, obj_id: str, auth: Auth, label: str):
    obj = db.get(model, obj_id)
    if obj is None or obj.merchant_id != auth.merchant.id or obj.mode != auth.mode:
        raise APIError(404, "not_found", f"{label} not found")
    return obj


def _merchant(db: Session, auth: Auth) -> Merchant:
    return db.get(Merchant, auth.merchant.id)


# ---- customers ------------------------------------------------------------------------------

class CustomerIn(BaseModel):
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    gstin: str | None = Field(default=None, pattern=GSTIN)
    state_code: str | None = Field(default=None, pattern=r"^\d{2}$")
    address: str | None = Field(default=None, max_length=500)
    metadata: dict[str, str] = {}


@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    c = Customer(id=new_id("cus"), merchant_id=auth.merchant.id, mode=auth.mode, email=body.email, name=body.name,
                 phone=body.phone, gstin=body.gstin, state_code=body.state_code or (body.gstin[:2] if body.gstin else None),
                 address=body.address, metadata_=body.metadata)
    db.add(c)
    db.commit()
    return billing.serialize_customer(c)


@router.get("/customers")
def list_customers(email: str | None = None, limit: int = 50, auth: Auth = Depends(require_secret_key),
                   db: Session = Depends(get_db)):
    q = db.query(Customer).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if email:
        q = q.filter_by(email=email)
    return {"data": [billing.serialize_customer(c) for c in q.order_by(Customer.created_at.desc()).limit(min(limit, 200))]}


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return billing.serialize_customer(_own(db, Customer, customer_id, auth, "Customer"))


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, body: CustomerIn, auth: Auth = Depends(scoped("billing")),
                    db: Session = Depends(get_db)):
    c = _own(db, Customer, customer_id, auth, "Customer")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(c, "metadata_" if field == "metadata" else field, value)
    db.commit()
    return billing.serialize_customer(c)


# ---- plans ----------------------------------------------------------------------------------

class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    interval: str = Field(pattern="^(day|week|month|year)$")
    interval_count: int = Field(default=1, ge=1, le=12)
    trial_days: int = Field(default=0, ge=0, le=730)
    tax_rate: float = Field(default=0, ge=0, le=40)
    hsn_sac: str | None = Field(default=None, max_length=10)


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    p = Plan(id=new_id("plan"), merchant_id=auth.merchant.id, mode=auth.mode, **{**body.model_dump(),
             "currency": body.currency.upper()}, provider_refs={})
    db.add(p)
    db.commit()
    return billing.serialize_plan(p)


@router.get("/plans")
def list_plans(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(Plan).filter_by(merchant_id=auth.merchant.id, mode=auth.mode).order_by(Plan.created_at.desc())
    return {"data": [billing.serialize_plan(p) for p in rows]}


@router.post("/plans/{plan_id}/archive")
def archive_plan(plan_id: str, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    p = _own(db, Plan, plan_id, auth, "Plan")
    p.active = False
    db.commit()
    return billing.serialize_plan(p)


# ---- subscriptions --------------------------------------------------------------------------

class SubscriptionIn(BaseModel):
    customer: str
    plan: str
    collection: str = Field(default="mandate", pattern="^(mandate|invoice)$",
                            description="mandate: auto-debit (UPI AutoPay, e-NACH, card, ACH/SEPA). "
                                        "invoice: a payable invoice link every cycle (works with any method, "
                                        "including zero-fee UPI).")
    mandate_method: str | None = None
    trial_days: int | None = Field(default=None, ge=0, le=730)
    quantity: int = Field(default=1, ge=1, le=10000)
    metadata: dict[str, str] = {}


@router.post("/subscriptions", status_code=201)
def create_subscription(body: SubscriptionIn, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    if body.mandate_method and body.mandate_method not in MANDATE_METHODS:
        raise APIError(400, "invalid_method", f"mandate_method must be one of {list(MANDATE_METHODS)}", "mandate_method")
    sub = billing.create_subscription(
        db, _merchant(db, auth), auth.mode, customer=_own(db, Customer, body.customer, auth, "Customer"),
        plan=_own(db, Plan, body.plan, auth, "Plan"), collection=body.collection, mandate_method=body.mandate_method,
        trial_days=body.trial_days, quantity=body.quantity, metadata=body.metadata)
    db.commit()
    out = billing.serialize_subscription(sub)
    first = db.query(Invoice).filter_by(subscription_id=sub.id).first()
    out["latest_invoice"] = billing.serialize_invoice(first) if first else None
    return out


@router.get("/subscriptions")
def list_subscriptions(status: str | None = None, customer: str | None = None,
                       auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    q = db.query(Subscription).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if status:
        q = q.filter_by(status=status)
    if customer:
        q = q.filter_by(customer_id=customer)
    return {"data": [billing.serialize_subscription(s) for s in q.order_by(Subscription.created_at.desc()).limit(200)]}


@router.get("/subscriptions/{sub_id}")
def get_subscription(sub_id: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return billing.serialize_subscription(_own(db, Subscription, sub_id, auth, "Subscription"))


class CancelIn(BaseModel):
    at_period_end: bool = False


@router.post("/subscriptions/{sub_id}/cancel")
def cancel_subscription(sub_id: str, body: CancelIn, auth: Auth = Depends(scoped("billing")),
                        db: Session = Depends(get_db)):
    sub = billing.cancel_subscription(db, _own(db, Subscription, sub_id, auth, "Subscription"), body.at_period_end)
    db.commit()
    return billing.serialize_subscription(sub)


class AdvanceIn(BaseModel):
    days: float = Field(gt=0, le=3650)


@router.post("/test_helpers/subscriptions/{sub_id}/advance")
def advance_subscription_clock(sub_id: str, body: AdvanceIn, auth: Auth = Depends(scoped("billing")),
                               db: Session = Depends(get_db)):
    """Test mode: fast-forward time for one subscription to see renewals, failures and dunning."""
    sub = _own(db, Subscription, sub_id, auth, "Subscription")
    summary = billing.advance_clock(db, sub, body.days)
    db.refresh(sub)
    return {"billing_run": summary, "subscription": billing.serialize_subscription(sub)}


# ---- invoices -------------------------------------------------------------------------------

class LineItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(default=1, ge=1, le=1_000_000)
    unit_amount: int = Field(ge=0)
    tax_rate: float = Field(default=0, ge=0, le=40)
    hsn_sac: str | None = Field(default=None, max_length=10)


class InvoiceIn(BaseModel):
    customer: str | None = None
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    line_items: list[LineItemIn] = Field(min_length=1, max_length=200)
    due_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=1000)
    finalize: bool = True


@router.post("/invoices", status_code=201)
def create_invoice(body: InvoiceIn, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    customer = _own(db, Customer, body.customer, auth, "Customer") if body.customer else None
    inv = billing.create_invoice(db, _merchant(db, auth), auth.mode, customer=customer,
                                 items=[i.model_dump() for i in body.line_items], currency=body.currency,
                                 due_days=body.due_days, notes=body.notes, finalize=body.finalize)
    db.commit()
    return billing.serialize_invoice(inv)


@router.get("/invoices")
def list_invoices(status: str | None = None, customer: str | None = None, subscription: str | None = None,
                  auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    q = db.query(Invoice).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    for field, value in (("status", status), ("customer_id", customer), ("subscription_id", subscription)):
        if value:
            q = q.filter_by(**{field: value})
    return {"data": [billing.serialize_invoice(i) for i in q.order_by(Invoice.created_at.desc()).limit(200)]}


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return billing.serialize_invoice(_own(db, Invoice, invoice_id, auth, "Invoice"))


@router.post("/invoices/{invoice_id}/finalize")
def finalize_invoice(invoice_id: str, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    inv = billing.finalize_invoice(db, _merchant(db, auth), _own(db, Invoice, invoice_id, auth, "Invoice"))
    db.commit()
    return billing.serialize_invoice(inv)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(invoice_id: str, auth: Auth = Depends(scoped("billing")), db: Session = Depends(get_db)):
    inv = billing.void_invoice(db, _own(db, Invoice, invoice_id, auth, "Invoice"))
    db.commit()
    return billing.serialize_invoice(inv)


class PaidIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


@router.post("/invoices/{invoice_id}/mark_paid")
def mark_invoice_paid(invoice_id: str, body: PaidIn, auth: Auth = Depends(scoped("billing")),
                      db: Session = Depends(get_db)):
    inv = billing.mark_paid_out_of_band(db, _own(db, Invoice, invoice_id, auth, "Invoice"), body.note)
    db.commit()
    return billing.serialize_invoice(inv)


# ---- payment links ----------------------------------------------------------------------------

class LinkIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    amount: int | None = Field(default=None, gt=0, description="Leave empty to let the customer choose")
    min_amount: int | None = Field(default=None, gt=0)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    payment_methods: list[str] | None = None
    max_payments: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


@router.post("/payment_links", status_code=201)
def create_link(body: LinkIn, auth: Auth = Depends(scoped("payments")), db: Session = Depends(get_db)):
    if body.payment_methods and any(m not in METHODS for m in body.payment_methods):
        raise APIError(400, "invalid_method", "Unknown payment method", "payment_methods")
    slug = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    expires = body.expires_at.replace(tzinfo=None) if body.expires_at else None
    link = PaymentLink(id=new_id("plink"), merchant_id=auth.merchant.id, mode=auth.mode, slug=slug, title=body.title,
                       description=body.description, amount=body.amount, min_amount=body.min_amount,
                       currency=body.currency.upper(), payment_methods=body.payment_methods,
                       max_payments=body.max_payments, expires_at=expires)
    db.add(link)
    db.commit()
    return billing.serialize_link(link)


@router.get("/payment_links")
def list_links(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(PaymentLink).filter_by(merchant_id=auth.merchant.id, mode=auth.mode).order_by(
        PaymentLink.created_at.desc()).limit(200)
    return {"data": [billing.serialize_link(link) for link in rows]}


class LinkUpdateIn(BaseModel):
    active: bool


@router.patch("/payment_links/{link_id}")
def update_link(link_id: str, body: LinkUpdateIn, auth: Auth = Depends(scoped("payments")),
                db: Session = Depends(get_db)):
    link = _own(db, PaymentLink, link_id, auth, "Payment link")
    link.active = body.active
    db.commit()
    return billing.serialize_link(link)
