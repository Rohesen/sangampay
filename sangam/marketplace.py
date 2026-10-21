"""Money movement after collection: marketplace splits, payouts to bank accounts, and disputes."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import vault
from .ledger import _post
from .models import (BankAccount, Dispute, PaymentAttempt, PaymentIntent, Payout, ProviderAccount, Transfer)
from .providers import REGISTRY, ProviderError
from .routing import context_for
from .util import APIError, iso, new_id, utcnow
from .webhooks import emit


# ---- splits ---------------------------------------------------------------------------------

def serialize_transfer(t: Transfer) -> dict:
    return {"id": t.id, "object": "transfer", "payment_intent": t.intent_id, "destination": t.destination,
            "amount": t.amount, "currency": t.currency, "status": t.status, "provider_ref": t.provider_ref,
            "error": t.error, "created": iso(t.created_at)}


def execute_transfers(db: Session, intent: PaymentIntent, attempt: PaymentAttempt) -> None:
    provider = REGISTRY[attempt.provider]
    account = db.get(ProviderAccount, attempt.provider_account_id) if attempt.provider_account_id else None
    for spec in intent.transfers or []:
        t = Transfer(id=new_id("tr"), intent_id=intent.id, destination=spec["account"], amount=int(spec["amount"]),
                     currency=intent.currency, status="pending")
        db.add(t)
        if not provider.supports_transfers:
            t.status, t.error = "failed", f"{provider.name} cannot split payments; settle this share manually"
        else:
            try:
                t.provider_ref = provider.transfer(context_for(account, intent.mode), intent, attempt,
                                                   t.destination, t.amount)
                t.status = "succeeded"
            except ProviderError as exc:
                t.status, t.error = "failed", str(exc)[:300]
        db.flush()
        emit(db, intent.merchant_id, intent.mode, f"transfer.{t.status}", serialize_transfer(t))


# ---- payouts --------------------------------------------------------------------------------

def serialize_payout(p: Payout) -> dict:
    return {"id": p.id, "object": "payout", "bank_account": p.bank_account_id, "amount": p.amount,
            "currency": p.currency, "mode": p.transfer_mode, "purpose": p.purpose, "narration": p.narration,
            "status": p.status, "provider": p.provider, "provider_ref": p.provider_ref,
            "failure_reason": p.failure_reason, "created": iso(p.created_at)}


def create_payout(db: Session, merchant_id: str, mode: str, *, bank_account_id: str, amount: int, currency: str,
                  transfer_mode: str, purpose: str, narration: str | None) -> Payout:
    ba = db.get(BankAccount, bank_account_id)
    if ba is None or ba.merchant_id != merchant_id or ba.mode != mode:
        raise APIError(404, "not_found", "Bank account not found", "bank_account")
    if transfer_mode == "UPI" and ba.kind != "upi_vpa":
        raise APIError(400, "invalid_mode", "UPI payouts need a UPI ID bank account", "mode")
    account = (db.query(ProviderAccount).filter_by(merchant_id=merchant_id, mode=mode, provider="razorpayx",
                                                   enabled=True).first())
    if account is not None:
        provider = REGISTRY["razorpayx"]
    elif mode == "test":
        provider = REGISTRY["mock"]
    else:
        raise APIError(400, "payouts_unavailable", "Connect RazorpayX to send payouts")
    p = Payout(id=new_id("po"), merchant_id=merchant_id, mode=mode, bank_account_id=ba.id, amount=amount,
               currency=currency.upper(), transfer_mode=transfer_mode, purpose=purpose, narration=narration,
               status="processing", provider=provider.name)
    db.add(p)
    db.flush()
    # The one place full bank details leave the vault: straight into the payout API call.
    bank = vault.decrypt(ba.enc_data, f"bank:{ba.merchant_id}:{ba.id}")
    try:
        p.status, p.provider_ref = provider.payout(context_for(account, mode), p, bank)
    except ProviderError as exc:
        p.status, p.failure_reason = "failed", str(exc)[:300]
    del bank
    if p.status == "processed":
        _post_payout(db, p)
    emit(db, merchant_id, mode, f"payout.{p.status}", serialize_payout(p))
    db.commit()
    return p


def _post_payout(db: Session, p: Payout) -> None:
    class _Ref:  # ledger helper only needs these attributes
        merchant_id, mode, currency = p.merchant_id, p.mode, p.currency
    _post(db, _Ref, f"payout:{p.id}", [("payouts:sent", p.amount), (f"bank:{p.provider}", -p.amount)])


def payout_webhook(db: Session, ref: str, status: str, reason: str | None) -> bool:
    p = db.query(Payout).filter_by(provider_ref=ref).first()
    if p is None or p.status == status:
        return False
    p.status, p.failure_reason = status, reason or p.failure_reason
    if status == "processed":
        _post_payout(db, p)
    emit(db, p.merchant_id, p.mode, f"payout.{status}", serialize_payout(p))
    db.commit()
    return True


# ---- disputes -------------------------------------------------------------------------------

def serialize_dispute(d: Dispute) -> dict:
    return {"id": d.id, "object": "dispute", "payment_intent": d.intent_id, "amount": d.amount, "currency": d.currency,
            "reason": d.reason, "status": d.status, "provider": d.provider, "evidence": d.evidence,
            "due_by": iso(d.due_by), "created": iso(d.created_at)}


def _to_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    return None


def upsert_dispute(db: Session, provider: str, ref: str, status: str, data: dict) -> Dispute | None:
    d = db.query(Dispute).filter_by(provider_ref=ref).first()
    if d is None:
        attempt = db.query(PaymentAttempt).filter_by(provider_payment_id=data.get("payment_id")).first() \
            if data.get("payment_id") else None
        if attempt is None:
            return None
        intent = db.get(PaymentIntent, attempt.intent_id)
        d = Dispute(id=new_id("dp"), merchant_id=intent.merchant_id, mode=intent.mode, intent_id=intent.id,
                    amount=int(data.get("amount") or intent.amount), currency=intent.currency,
                    reason=str(data.get("reason") or "general")[:64], status=status, provider=provider,
                    provider_ref=ref, due_by=_to_dt(data.get("due_by")), evidence={})
        db.add(d)
        db.flush()
        emit(db, d.merchant_id, d.mode, "dispute.created", serialize_dispute(d))
    elif d.status != status:
        d.status = status
        emit(db, d.merchant_id, d.mode, f"dispute.{'closed' if status in ('won', 'lost') else 'updated'}",
             serialize_dispute(d))
    if status == "lost":
        _post_dispute_loss(db, d)
    db.commit()
    return d


def _post_dispute_loss(db: Session, d: Dispute) -> None:
    intent = db.get(PaymentIntent, d.intent_id)
    _post(db, intent, f"dispute:{d.id}", [("merchant:disputes", d.amount), (f"clearing:{intent.provider}", -d.amount)])


def submit_evidence(db: Session, d: Dispute, evidence: dict) -> Dispute:
    if d.status not in ("needs_response",):
        raise APIError(409, "invalid_state", "Evidence can only be submitted while the dispute needs a response")
    if d.provider != "mock":
        intent = db.get(PaymentIntent, d.intent_id)
        attempt = db.get(PaymentAttempt, intent.latest_attempt_id)
        account = db.get(ProviderAccount, attempt.provider_account_id) if attempt.provider_account_id else None
        try:
            REGISTRY[d.provider].submit_dispute_evidence(context_for(account, d.mode), d, evidence)
        except ProviderError as exc:
            raise APIError(502, "provider_error", str(exc)) from exc
    d.evidence = {**evidence, "submitted_at": iso(utcnow())}
    d.status = "under_review"
    emit(db, d.merchant_id, d.mode, "dispute.updated", serialize_dispute(d))
    db.commit()
    return d


def accept_dispute(db: Session, d: Dispute) -> Dispute:
    if d.status in ("won", "lost"):
        raise APIError(409, "invalid_state", "Dispute is already closed")
    d.status = "lost"
    _post_dispute_loss(db, d)
    emit(db, d.merchant_id, d.mode, "dispute.closed", serialize_dispute(d))
    db.commit()
    return d
