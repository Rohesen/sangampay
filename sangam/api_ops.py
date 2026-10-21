"""Operations API: business settings, team keys + roles, audit log, reconciliation, disputes, payouts,
marketplace transfers, analytics and exports."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import analytics, marketplace, recon
from .db import get_db
from .models import (ApiKey, AuditLog, Dispute, Merchant, PaymentIntent, Payout, ReconRun, Transfer)
from .providers import REGISTRY
from .risk import DEFAULT_RULES, rules_for
from .routing import DEFAULT_FEES, provider_health
from .security import ROLES, ROLE_SCOPES, Auth, generate_secret_key, hash_key, require_secret_key, scoped
from .util import APIError, iso, new_id, utcnow

router = APIRouter(prefix="/v1")


def _merchant(db: Session, auth: Auth) -> Merchant:
    return db.get(Merchant, auth.merchant.id)


# ---- business settings ----------------------------------------------------------------------

class ProfileIn(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    gstin: str | None = Field(default=None, pattern=r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
    state_code: str | None = Field(default=None, pattern=r"^\d{2}$")
    address: str | None = Field(default=None, max_length=500)
    support_email: str | None = Field(default=None, max_length=254)
    invoice_prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,8}$")
    logo_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")


@router.put("/merchant/profile")
def set_profile(body: ProfileIn, auth: Auth = Depends(scoped("settings")), db: Session = Depends(get_db)):
    m = _merchant(db, auth)
    profile = {**(m.profile or {}), **body.model_dump(exclude_unset=True)}
    if profile.get("gstin") and not profile.get("state_code"):
        profile["state_code"] = profile["gstin"][:2]
    m.profile = profile
    db.commit()
    return {"profile": m.profile}


class SettingsIn(BaseModel):
    routing_strategy: str | None = Field(default=None, pattern="^(priority|success_rate|cost)$")
    fees: dict[str, float] | None = None
    invoice_due_days: int | None = Field(default=None, ge=0, le=365)
    dunning_days: list[int] | None = Field(default=None, max_length=8)
    dunning_final_action: str | None = Field(default=None, pattern="^(unpaid|cancel)$")


@router.put("/merchant/settings")
def set_settings(body: SettingsIn, auth: Auth = Depends(scoped("settings")), db: Session = Depends(get_db)):
    m = _merchant(db, auth)
    data = body.model_dump(exclude_unset=True)
    if "routing_strategy" in data:
        m.routing_strategy = data.pop("routing_strategy")
    if data.get("fees") and any(p not in REGISTRY for p in data["fees"]):
        raise APIError(400, "invalid_provider", "Unknown provider in fees", "fees")
    m.settings = {**(m.settings or {}), **data}
    db.commit()
    return {"routing_strategy": m.routing_strategy, "settings": m.settings,
            "effective_fees": {**DEFAULT_FEES, **(m.settings.get("fees") or {})}}


class RiskIn(BaseModel):
    max_amount: int | None = Field(default=None, gt=0)
    block_emails: list[str] | None = Field(default=None, max_length=5000)
    block_ips: list[str] | None = Field(default=None, max_length=5000)
    allowed_countries: list[str] | None = Field(default=None, max_length=250)
    max_attempts_per_email_hour: int | None = Field(default=None, ge=1, le=1000)
    max_attempts_per_ip_hour: int | None = Field(default=None, ge=1, le=10000)
    review_score: int | None = Field(default=None, ge=0, le=100)
    block_score: int | None = Field(default=None, ge=1, le=101)


@router.put("/merchant/risk_rules")
def set_risk_rules(body: RiskIn, auth: Auth = Depends(scoped("risk")), db: Session = Depends(get_db)):
    m = _merchant(db, auth)
    m.risk_rules = {**(m.risk_rules or {}), **body.model_dump(exclude_unset=True)}
    db.commit()
    return {"risk_rules": rules_for(m), "defaults": DEFAULT_RULES}


@router.get("/routing/health")
def routing_health(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    return {"strategy": auth.merchant.routing_strategy or "priority", "data": provider_health(db, auth.merchant)}


# ---- team keys --------------------------------------------------------------------------------

def _serialize_key(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "role": k.role, "mode": k.mode, "display": k.display, "revoked": k.revoked,
            "last_used": iso(k.last_used_at), "created": iso(k.created_at)}


@router.get("/api_keys")
def list_keys(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(ApiKey).filter_by(merchant_id=auth.merchant.id, mode=auth.mode).order_by(ApiKey.created_at)
    return {"data": [_serialize_key(k) for k in rows],
            "roles": {r: sorted(ROLE_SCOPES[r]) for r in ROLES}}


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern="^(admin|developer|support|viewer)$")


@router.post("/api_keys", status_code=201)
def create_key(body: KeyIn, auth: Auth = Depends(scoped("keys")), db: Session = Depends(get_db)):
    raw, display = generate_secret_key(auth.mode)
    k = ApiKey(id=new_id("key"), merchant_id=auth.merchant.id, mode=auth.mode, display=display,
               key_hash=hash_key(raw), name=body.name, role=body.role)
    db.add(k)
    db.commit()
    return {**_serialize_key(k), "secret_key": raw, "note": "Shown once. Share it with this teammate securely."}


@router.delete("/api_keys/{key_id}")
def revoke_key(key_id: str, auth: Auth = Depends(scoped("keys")), db: Session = Depends(get_db)):
    k = db.get(ApiKey, key_id)
    if k is None or k.merchant_id != auth.merchant.id:
        raise APIError(404, "not_found", "Key not found")
    if k.id == auth.key.id:
        raise APIError(400, "cannot_revoke_self", "Use another admin key to revoke this one")
    k.revoked = True
    db.commit()
    return _serialize_key(k)


@router.get("/audit_logs")
def audit_logs(limit: int = 100, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = (db.query(AuditLog).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
            .order_by(AuditLog.id.desc()).limit(min(limit, 500)))
    return {"data": [{"actor": a.actor, "key": a.key_id, "method": a.method, "path": a.path, "status": a.status_code,
                      "ip": a.ip, "created": iso(a.created_at)} for a in rows]}


# ---- reconciliation ---------------------------------------------------------------------------

class ReconIn(BaseModel):
    csv: str | None = Field(default=None, max_length=10_000_000, description="Bank statement CSV export")
    rows: list[dict] | None = Field(default=None, description='[{"amount": 49900, "text": "UPI/412345678901/..."}]')
    currency: str = "INR"
    auto_approve: bool = True


def _serialize_run(r: ReconRun) -> dict:
    return {"id": r.id, "object": "reconciliation", "source": r.source, "rows": r.rows, "matched": r.matched,
            "report": r.report, "created": iso(r.created_at)}


@router.post("/reconciliation", status_code=201)
def run_reconciliation(body: ReconIn, auth: Auth = Depends(scoped("recon")), db: Session = Depends(get_db)):
    if body.csv:
        rows, source = recon.parse_csv(body.csv, body.currency), "csv"
    elif body.rows is not None:
        rows, source = body.rows, "api"
        if any("amount" not in r for r in rows):
            raise APIError(400, "invalid_rows", "Every row needs an amount (minor units) and text", "rows")
    else:
        raise APIError(400, "missing_field", "Send a bank statement as 'csv' or 'rows'")
    return _serialize_run(recon.reconcile(db, auth.merchant.id, auth.mode, rows, source, body.auto_approve))


@router.get("/reconciliation")
def list_reconciliation(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(ReconRun).filter_by(merchant_id=auth.merchant.id, mode=auth.mode).order_by(
        ReconRun.created_at.desc()).limit(50)
    return {"data": [_serialize_run(r) for r in rows]}


# ---- disputes -------------------------------------------------------------------------------

def _dispute(db: Session, auth: Auth, dispute_id: str) -> Dispute:
    d = db.get(Dispute, dispute_id)
    if d is None or d.merchant_id != auth.merchant.id or d.mode != auth.mode:
        raise APIError(404, "not_found", "Dispute not found")
    return d


@router.get("/disputes")
def list_disputes(status: str | None = None, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    q = db.query(Dispute).filter_by(merchant_id=auth.merchant.id, mode=auth.mode)
    if status:
        q = q.filter_by(status=status)
    return {"data": [marketplace.serialize_dispute(d) for d in q.order_by(Dispute.created_at.desc()).limit(200)]}


class EvidenceIn(BaseModel):
    summary: str = Field(min_length=10, max_length=20000)
    customer_email_address: str | None = None
    product_description: str | None = Field(default=None, max_length=5000)
    shipping_tracking_number: str | None = Field(default=None, max_length=100)


@router.post("/disputes/{dispute_id}/evidence")
def submit_evidence(dispute_id: str, body: EvidenceIn, auth: Auth = Depends(scoped("disputes")),
                    db: Session = Depends(get_db)):
    d = marketplace.submit_evidence(db, _dispute(db, auth, dispute_id), body.model_dump(exclude_none=True))
    return marketplace.serialize_dispute(d)


@router.post("/disputes/{dispute_id}/accept")
def accept_dispute(dispute_id: str, auth: Auth = Depends(scoped("disputes")), db: Session = Depends(get_db)):
    return marketplace.serialize_dispute(marketplace.accept_dispute(db, _dispute(db, auth, dispute_id)))


class TestDisputeIn(BaseModel):
    reason: str = "product_not_received"
    outcome: str | None = Field(default=None, pattern="^(won|lost)$")


@router.post("/test_helpers/payment_intents/{intent_id}/dispute", status_code=201)
def simulate_dispute(intent_id: str, body: TestDisputeIn, auth: Auth = Depends(scoped("disputes")),
                     db: Session = Depends(get_db)):
    if auth.mode != "test":
        raise APIError(400, "test_mode_only", "Simulated disputes are only available in test mode")
    intent = db.get(PaymentIntent, intent_id)
    if intent is None or intent.merchant_id != auth.merchant.id or intent.status not in (
            "succeeded", "partially_refunded"):
        raise APIError(404, "not_found", "Succeeded payment not found")
    d = Dispute(id=new_id("dp"), merchant_id=intent.merchant_id, mode="test", intent_id=intent.id, amount=intent.amount,
                currency=intent.currency, reason=body.reason, status="needs_response", provider="mock",
                provider_ref=f"mockdp_{new_id('x', 12)}", due_by=utcnow() + timedelta(days=7), evidence={})
    db.add(d)
    db.flush()
    from .webhooks import emit
    emit(db, d.merchant_id, d.mode, "dispute.created", marketplace.serialize_dispute(d))
    db.commit()
    if body.outcome:
        marketplace.upsert_dispute(db, "mock", d.provider_ref, body.outcome, {})
    return marketplace.serialize_dispute(d)


# ---- payouts & transfers --------------------------------------------------------------------

class PayoutIn(BaseModel):
    bank_account: str
    amount: int = Field(gt=0)
    currency: str = "INR"
    mode: str = Field(default="IMPS", pattern="^(IMPS|NEFT|RTGS|UPI)$")
    purpose: str = Field(default="payout", pattern="^(payout|refund|cashback|salary|vendor bill|utility bill)$")
    narration: str | None = Field(default=None, max_length=30)


@router.post("/payouts", status_code=201)
def create_payout(body: PayoutIn, auth: Auth = Depends(scoped("payouts")), db: Session = Depends(get_db)):
    if body.mode == "RTGS" and body.amount < 2_00_000_00:
        raise APIError(400, "invalid_mode", "RTGS is for ₹2,00,000 and above; use IMPS or NEFT", "mode")
    p = marketplace.create_payout(db, auth.merchant.id, auth.mode, bank_account_id=body.bank_account,
                                  amount=body.amount, currency=body.currency, transfer_mode=body.mode,
                                  purpose=body.purpose, narration=body.narration)
    return marketplace.serialize_payout(p)


@router.get("/payouts")
def list_payouts(auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    rows = db.query(Payout).filter_by(merchant_id=auth.merchant.id, mode=auth.mode).order_by(
        Payout.created_at.desc()).limit(200)
    return {"data": [marketplace.serialize_payout(p) for p in rows]}


@router.get("/transfers")
def list_transfers(payment_intent: str | None = None, auth: Auth = Depends(require_secret_key),
                   db: Session = Depends(get_db)):
    q = (db.query(Transfer).join(PaymentIntent, PaymentIntent.id == Transfer.intent_id)
         .filter(PaymentIntent.merchant_id == auth.merchant.id, PaymentIntent.mode == auth.mode))
    if payment_intent:
        q = q.filter(Transfer.intent_id == payment_intent)
    return {"data": [marketplace.serialize_transfer(t) for t in q.order_by(Transfer.created_at.desc()).limit(200)]}


# ---- analytics & exports ----------------------------------------------------------------------

@router.get("/analytics")
def get_analytics(days: int = 30, currency: str | None = None, auth: Auth = Depends(require_secret_key),
                  db: Session = Depends(get_db)):
    return analytics.overview(db, auth.merchant.id, auth.mode, max(1, min(days, 365)),
                              currency.upper() if currency else None)


@router.get("/exports/{kind}.csv", response_class=PlainTextResponse)
def export(kind: str, auth: Auth = Depends(require_secret_key), db: Session = Depends(get_db)):
    if kind not in ("payments", "refunds", "invoices"):
        raise APIError(404, "not_found", "Exports: payments, refunds, invoices")
    return PlainTextResponse(analytics.export_csv(db, auth.merchant.id, auth.mode, kind), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="sangampay-{kind}.csv"'})
