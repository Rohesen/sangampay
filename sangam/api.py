"""Merchant REST API (/v1). Authenticate with 'Authorization: Bearer sk_test_...'."""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import ledger, payments, vault
from .config import settings
from .db import get_db
from .models import (ApiKey, BankAccount, Event, Merchant, PaymentIntent, ProviderAccount, Refund,
                     WebhookDelivery, WebhookEndpoint)
from .providers import DEFAULT_ROUTES, METHODS, REGISTRY
from .routing import available_methods
from .security import ROLES, Auth, generate_secret_key, hash_key, require_secret_key, scoped
from .util import APIError, iso, new_id
from .webhooks import event_payload, validate_url

router = APIRouter(prefix="/v1")


# ---- helpers ------------------------------------------------------------------------------

def idempotent(db: Session, auth: Auth, key: str | None, payload: dict, fn: Callable[[], tuple[int, dict]]):
    """Replays the stored response for a repeated Idempotency-Key; rejects reuse with a different body."""
    if not key:
        status, body = fn()
        return JSONResponse(body, status_code=status)
    from .models import IdempotencyRecord

    scoped = f"{auth.mode}:{key}"
    req_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    existing = db.get(IdempotencyRecord, (auth.merchant.id, scoped))
    if existing:
        if existing.request_hash != req_hash:
            raise APIError(422, "idempotency_conflict", "Idempotency-Key was reused with a different request")
        return JSONResponse(existing.body, status_code=existing.status_code, headers={"Idempotent-Replayed": "true"})
    status, body = fn()
    db.add(IdempotencyRecord(merchant_id=auth.merchant.id, key=scoped, request_hash=req_hash,
                             status_code=status, body=body))
    db.commit()
    return JSONResponse(body, status_code=status)


def get_intent(db: Session, auth: Auth, intent_id: str) -> PaymentIntent:
    intent = db.get(PaymentIntent, intent_id)
    if intent is None or intent.merchant_id != auth.merchant.id or intent.mode != auth.mode:
        raise APIError(404, "not_found", "Payment intent not found")
    return intent


# ---- merchants & keys ---------------------------------------------------------------------

class MerchantIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    country: str = Field(default="IN", pattern=r"^[A-Z]{2}$")


@router.post("/merchants", status_code=201)
def create_merchant(body: MerchantIn, db: Session = Depends(get_db)):
    if not settings.allow_signup:
        raise APIError(403, "signup_disabled", "Sign-up is disabled on this server (SANGAM_ALLOW_SIGNUP)")
    merchant = Merchant(id=new_id("acct"), name=body.name, email=body.email, country=body.country, routing={})
    db.add(merchant)
    db.flush()  # insert the merchant before its keys (FK order)
    keys = {}
    for mode in ("test", "live"):
        raw, display = generate_secret_key(mode)
        db.add(ApiKey(id=new_id("key"), merchant_id=merchant.id, mode=mode, display=display, key_hash=hash_key(raw),
                      name="Owner key", role="admin"))
        keys[f"secret_key_{mode}"] = raw
    db.commit()
    return {"merchant": {"id": merchant.id, "name": merchant.name, "country": merchant.country},
            **keys, "note": "Store these keys now. Sangam keeps only their hashes and cannot show them again."}


@router.get("/merchant")
def get_merchant(auth: Auth = Depends(require_secret_key)):
    m = auth.merchant
    from .risk import rules_for

    return {"id": m.id, "name": m.name, "email": m.email, "country": m.country, "mode": auth.mode,
            "key": {"name": auth.key.name, "role": auth.key.role},
            "routing": {**DEFAULT_ROUTES, **(m.routing or {})}, "routing_strategy": m.routing_strategy or "priority",
            "profile": m.profile or {}, "settings": m.settings or {}, "risk_rules": rules_for(m)}


@router.post("/api_keys/roll")
def roll_key(auth: Auth = Depends(scoped("keys")), authorization: str = Header(...),
             db: Session = Depends(get_db)):
    current = db.query(ApiKey).filter_by(key_hash=hash_key(authorization[7:].strip())).one()
    current.revoked = True
    raw, display = generate_secret_key(auth.mode)
    db.add(ApiKey(id=new_id("key"), merchant_id=auth.merchant.id, mode=auth.mode, display=display,
                  key_hash=hash_key(raw), name=current.name, role=current.role))
    db.commit()
    return {"secret_key": raw, "revoked": current.display}


class RoutingIn(BaseModel):
    routing: dict[str, list[str]]


@router.put("/merchant/routing")
def set_routing(body: RoutingIn, auth: Auth = Depends(scoped("settings")), db: Session = Depends(get_db)):
    for method, providers in body.routing.items():
        if method not in METHODS:
            raise APIError(400, "invalid_method", f"Unknown method '{method}'", "routing")
        bad = [p for p in providers if p not in REGISTRY or method not in REGISTRY[p].methods]
        if bad:
            raise APIError(400, "invalid_provider", f"{bad} cannot process {method}", "routing")
    merchant = db.get(Merchant, auth.merchant.id)
    merchant.routing = body.routing
    db.commit()
    return {"routing": {**DEFAULT_ROUTES, **merchant.routing}}


# ---- gateway connections ------------------------------------------------------------------

@router.get("/providers")
def list_providers(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    accounts = {a.provider: a for a in db.query(ProviderAccount).filter_by(merchant_id=auth.merchant.id,
                                                                          mode=auth.mode)}
    out = []
    for p in REGISTRY.values():
        if p.test_only and auth.mode != "test":
            continue
        acct = accounts.get(p.name)
        out.append({"name": p.name, "display_name": p.display_name, "methods": sorted(p.methods),
                    "currencies": sorted(p.currencies) if p.currencies else "any",
                    "credential_fields": p.credential_fields, "config_fields": p.config_fields,
                    "connected": (acct is not None and acct.enabled) or not p.needs_account,
                    "config": acct.config if acct else {},
                    "webhook_url": f"{settings.base_url}/providers/{p.name}/webhook/{acct.id}" if acct else None})
    return {"data": out}


class ProviderIn(BaseModel):
    credentials: dict[str, str] = {}
    config: dict[str, str] = {}
    enabled: bool = True


@router.put("/providers/{name}")
def connect_provider(name: str, body: ProviderIn, auth: Auth = Depends(scoped("settings")),
                     db: Session = Depends(get_db)):
    provider = REGISTRY.get(name)
    if provider is None or not provider.needs_account:
        raise APIError(404, "not_found", f"Unknown provider '{name}'")
    provider.validate_setup(body.credentials, body.config)
    acct = db.query(ProviderAccount).filter_by(merchant_id=auth.merchant.id, provider=name, mode=auth.mode).first()
    if acct is None:
        acct = ProviderAccount(id=new_id("pacct"), merchant_id=auth.merchant.id, provider=name, mode=auth.mode)
        db.add(acct)
    acct.config, acct.enabled = body.config, body.enabled
    acct.enc_credentials = vault.encrypt(body.credentials, f"provider:{auth.merchant.id}:{acct.id}") \
        if body.credentials else None
    db.commit()
    return {"provider": name, "connected": True, "config": acct.config,
            "credentials": {k: vault.mask(v) for k, v in body.credentials.items()},
            "webhook_url": f"{settings.base_url}/providers/{name}/webhook/{acct.id}"}


@router.delete("/providers/{name}")
def disconnect_provider(name: str, auth: Auth = Depends(scoped("settings")), db: Session = Depends(get_db)):
    acct = db.query(ProviderAccount).filter_by(merchant_id=auth.merchant.id, provider=name, mode=auth.mode).first()
    if acct is None:
        raise APIError(404, "not_found", "Provider not connected")
    acct.enabled, acct.enc_credentials = False, None  # keep the row: past attempts reference it
    db.commit()
    return {"provider": name, "connected": False}


# ---- payments -----------------------------------------------------------------------------

class CustomerIn(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class TransferIn(BaseModel):
    account: str = Field(min_length=3, max_length=120, description="Razorpay Route acc_... or Stripe Connect acct_...")
    amount: int = Field(gt=0)


class IntentIn(BaseModel):
    amount: int = Field(gt=0, le=10**13, description="In the currency's smallest unit, e.g. paise")
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    description: str | None = Field(default=None, max_length=500)
    customer: CustomerIn = CustomerIn()
    payment_methods: list[str] | None = None
    return_url: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, str] = {}
    customer_id: str | None = None
    transfers: list[TransferIn] | None = Field(default=None, max_length=20)

    @field_validator("metadata")
    @classmethod
    def _small_metadata(cls, v: dict) -> dict:
        if len(v) > 50 or any(len(k) > 40 or len(val) > 500 for k, val in v.items()):
            raise ValueError("metadata allows 50 keys, 40-char keys and 500-char values")
        return v

    @field_validator("return_url")
    @classmethod
    def _https_url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("return_url must be an http(s) URL")
        return v


@router.post("/payment_intents")
def create_payment_intent(body: IntentIn, auth: Auth = Depends(scoped("payments")), db: Session = Depends(get_db),
                          idempotency_key: str | None = Header(default=None)):
    def run():
        customer = body.customer.model_dump(exclude_none=True)
        if body.customer_id:
            from .models import Customer
            c = db.get(Customer, body.customer_id)
            if c is None or c.merchant_id != auth.merchant.id or c.mode != auth.mode:
                raise APIError(404, "not_found", "Customer not found", "customer_id")
            customer = {**{k: v for k, v in {"email": c.email, "name": c.name, "phone": c.phone}.items() if v},
                        **customer}
        intent = payments.create_intent(
            db, auth.merchant.id, auth.mode, amount=body.amount, currency=body.currency, description=body.description,
            customer=customer, payment_methods=body.payment_methods, return_url=body.return_url,
            metadata=body.metadata, customer_id=body.customer_id,
            transfers=[t.model_dump() for t in body.transfers] if body.transfers else None)
        db.commit()
        return 201, payments.serialize_intent(intent, include_secret=True)
    return idempotent(db, auth, idempotency_key, body.model_dump(), run)


@router.get("/payment_intents")
def list_payment_intents(limit: int = 25, status: str | None = None, starting_after: str | None = None,
                         auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    q = db.query(PaymentIntent).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if status:
        q = q.filter_by(status=status)
    if starting_after:
        cursor = get_intent(db, auth, starting_after)
        q = q.filter(PaymentIntent.created_at < cursor.created_at)
    rows = q.order_by(PaymentIntent.created_at.desc()).limit(min(max(limit, 1), 100) + 1).all()
    return {"data": [payments.serialize_intent(i) for i in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/payment_intents/{intent_id}")
def retrieve_payment_intent(intent_id: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return payments.serialize_intent(get_intent(db, auth, intent_id), include_secret=True)


class ConfirmIn(BaseModel):
    method: str
    method_data: dict = {}


@router.post("/payment_intents/{intent_id}/confirm")
def confirm_server_side(intent_id: str, body: ConfirmIn, auth: Auth = Depends(scoped("payments")),
                        db: Session = Depends(get_db)):
    intent = payments.confirm(db, get_intent(db, auth, intent_id), body.method, body.method_data)
    return payments.serialize_intent(intent)


@router.post("/payment_intents/{intent_id}/cancel")
def cancel_payment_intent(intent_id: str, auth: Auth = Depends(scoped("payments")), db: Session = Depends(get_db)):
    return payments.serialize_intent(payments.cancel(db, get_intent(db, auth, intent_id)))


class VerifyIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/payment_intents/{intent_id}/verify")
def verify_upi_payment(intent_id: str, body: VerifyIn, auth: Auth = Depends(scoped("payments")),
                       db: Session = Depends(get_db)):
    """Confirm (or reject) a direct-UPI payment after checking the UTR in your bank statement."""
    return payments.serialize_intent(payments.verify_manual(db, get_intent(db, auth, intent_id),
                                                            body.approve, body.note))


@router.get("/payment_intents/{intent_id}/attempts")
def list_attempts(intent_id: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    from .models import PaymentAttempt

    get_intent(db, auth, intent_id)
    rows = db.query(PaymentAttempt).filter_by(intent_id=intent_id).order_by(PaymentAttempt.created_at).all()
    return {"data": [{"id": a.id, "provider": a.provider, "method": a.method, "status": a.status,
                      "provider_ref": a.provider_ref, "error": a.error, "created": iso(a.created_at)} for a in rows]}


@router.get("/payment_methods")
def payment_methods(currency: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return {"data": available_methods(db, auth.merchant, auth.mode, currency.upper())}


# ---- refunds ------------------------------------------------------------------------------

class RefundIn(BaseModel):
    payment_intent: str
    amount: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=200)
    bank_account: str | None = None


@router.post("/refunds")
def create_refund(body: RefundIn, auth: Auth = Depends(scoped("refunds")), db: Session = Depends(get_db),
                  idempotency_key: str | None = Header(default=None)):
    def run():
        r = payments.refund(db, get_intent(db, auth, body.payment_intent), body.amount, body.reason,
                            body.bank_account)
        return 201, payments.serialize_refund(r)
    return idempotent(db, auth, idempotency_key, body.model_dump(), run)


@router.get("/refunds")
def list_refunds(payment_intent: str | None = None, auth: Auth = Depends(require_secret_key),
                 db: Session = Depends(get_db)):
    q = db.query(Refund).join(PaymentIntent, PaymentIntent.id == Refund.intent_id).filter(
        Refund.merchant_id == auth.merchant.id, PaymentIntent.mode == auth.mode)
    if payment_intent:
        q = q.filter(Refund.intent_id == payment_intent)
    return {"data": [payments.serialize_refund(r) for r in q.order_by(Refund.created_at.desc()).limit(100)]}


class ManualRefundIn(BaseModel):
    reference: str | None = Field(default=None, max_length=120)


@router.post("/refunds/{refund_id}/mark_completed")
def mark_refund_completed(refund_id: str, body: ManualRefundIn, auth: Auth = Depends(scoped("refunds")),
                          db: Session = Depends(get_db)):
    r = db.get(Refund, refund_id)
    if r is None or r.merchant_id != auth.merchant.id:
        raise APIError(404, "not_found", "Refund not found")
    return payments.serialize_refund(payments.complete_manual_refund(db, r, body.reference))


# ---- bank account vault -------------------------------------------------------------------

class BankAccountIn(BaseModel):
    kind: str = Field(description="in_bank | iban | us_ach | upi_vpa")
    owner: str = Field(default="customer", pattern="^(merchant|customer)$")
    customer_ref: str | None = Field(default=None, max_length=120)
    details: dict[str, str]


def serialize_bank_account(ba: BankAccount) -> dict:
    return {"id": ba.id, "object": "bank_account", "kind": ba.kind, "owner": ba.owner,
            "customer_ref": ba.customer_ref, "details": ba.masked, "created": iso(ba.created_at)}


@router.post("/bank_accounts", status_code=201)
def store_bank_account(body: BankAccountIn, auth: Auth = Depends(scoped("vault")), db: Session = Depends(get_db)):
    """Encrypts and stores bank details. Write-only: the API only ever returns the masked view."""
    secret, masked, fp_source = vault.normalize_bank_details(body.kind, body.details)
    fp = vault.fingerprint(fp_source, f"{auth.merchant.id}:{body.kind}")
    existing = db.query(BankAccount).filter_by(merchant_id=auth.merchant.id, mode=auth.mode, fingerprint=fp).first()
    if existing:
        return JSONResponse(serialize_bank_account(existing), status_code=200)
    ba_id = new_id("ba")
    ba = BankAccount(id=ba_id, merchant_id=auth.merchant.id, mode=auth.mode, owner=body.owner,
                     customer_ref=body.customer_ref, kind=body.kind, masked=masked, fingerprint=fp,
                     enc_data=vault.encrypt(secret, f"bank:{auth.merchant.id}:{ba_id}"))
    db.add(ba)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        ba = db.query(BankAccount).filter_by(merchant_id=auth.merchant.id, mode=auth.mode, fingerprint=fp).one()
    return serialize_bank_account(ba)


@router.get("/bank_accounts")
def list_bank_accounts(customer_ref: str | None = None, auth: Auth = Depends(require_secret_key),
                       db: Session = Depends(get_db)):
    q = db.query(BankAccount).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if customer_ref:
        q = q.filter_by(customer_ref=customer_ref)
    return {"data": [serialize_bank_account(b) for b in q.order_by(BankAccount.created_at.desc()).limit(100)]}


@router.delete("/bank_accounts/{ba_id}")
def delete_bank_account(ba_id: str, auth: Auth = Depends(scoped("vault")), db: Session = Depends(get_db)):
    ba = db.get(BankAccount, ba_id)
    if ba is None or ba.merchant_id != auth.merchant.id or ba.mode != auth.mode:
        raise APIError(404, "not_found", "Bank account not found")
    db.delete(ba)  # crypto-shredding: the per-record data key goes with the row
    db.commit()
    return {"id": ba_id, "deleted": True}


# ---- webhooks, events, balance -------------------------------------------------------------

class WebhookIn(BaseModel):
    url: str = Field(max_length=2000)
    events: list[str] = ["*"]


@router.post("/webhook_endpoints", status_code=201)
def create_webhook_endpoint(body: WebhookIn, auth: Auth = Depends(scoped("webhooks")), db: Session = Depends(get_db)):
    validate_url(body.url)
    ep_id, secret = new_id("we"), f"whsec_{secrets.token_urlsafe(32)}"
    db.add(WebhookEndpoint(id=ep_id, merchant_id=auth.merchant.id, mode=auth.mode, url=body.url, events=body.events,
                           enc_secret=vault.encrypt({"secret": secret}, f"webhook:{auth.merchant.id}:{ep_id}")))
    db.commit()
    return {"id": ep_id, "url": body.url, "events": body.events, "secret": secret,
            "note": "Verify the Sangam-Signature header with this secret. It is shown only once."}


@router.get("/webhook_endpoints")
def list_webhook_endpoints(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(WebhookEndpoint).filter_by(merchant_id=auth.merchant.id, mode=auth.mode, enabled=True)
    return {"data": [{"id": e.id, "url": e.url, "events": e.events, "created": iso(e.created_at)} for e in rows]}


@router.delete("/webhook_endpoints/{endpoint_id}")
def delete_webhook_endpoint(endpoint_id: str, auth: Auth = Depends(scoped("webhooks")),
                            db: Session = Depends(get_db)):
    ep = db.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.merchant_id != auth.merchant.id:
        raise APIError(404, "not_found", "Webhook endpoint not found")
    ep.enabled = False
    db.query(WebhookDelivery).filter_by(endpoint_id=ep.id, status="pending").update({"status": "failed"})
    db.commit()
    return {"id": endpoint_id, "deleted": True}


@router.get("/events")
def list_events(limit: int = 25, type: str | None = None, auth: Auth = Depends(require_secret_key),
                db: Session = Depends(get_db)):
    q = db.query(Event).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if type:
        q = q.filter_by(type=type)
    return {"data": [event_payload(e) for e in q.order_by(Event.created_at.desc()).limit(min(limit, 100))]}


@router.post("/events/{event_id}/resend")
def resend_event(event_id: str, auth: Auth = Depends(scoped("webhooks")), db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if event is None or event.merchant_id != auth.merchant.id:
        raise APIError(404, "not_found", "Event not found")
    count = 0
    for ep in db.query(WebhookEndpoint).filter_by(merchant_id=auth.merchant.id, mode=event.mode, enabled=True):
        db.add(WebhookDelivery(id=new_id("whd"), endpoint_id=ep.id, event_id=event.id))
        count += 1
    db.commit()
    return {"event": event_id, "queued_deliveries": count}


@router.get("/balance")
def get_balance(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return {"object": "balance", "livemode": auth.mode == "live",
            "data": ledger.balance(db, auth.merchant.id, auth.mode),
            "note": "Sangam is non-custodial: these funds settle directly into your connected gateway/bank accounts."}
