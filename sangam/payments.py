"""Payment lifecycle: create -> confirm (routed) -> requires_action -> succeeded / failed -> refunds.

Statuses: requires_payment_method, requires_action, pending_verification (UPI direct, UTR sent),
processing (bank debits clearing), succeeded, partially_refunded, refunded, canceled.
A failed attempt returns the intent to requires_payment_method so the customer can retry."""
from __future__ import annotations

import re
import secrets

from sqlalchemy.orm import Session

from . import ledger, risk
from .config import settings
from .models import BankAccount, PaymentAttempt, PaymentIntent, ProviderAccount, Refund
from .providers import METHODS, REGISTRY, ProviderError
from .routing import breaker, context_for, record_outcome, routes_for
from .util import APIError, iso, new_id
from .webhooks import emit

OPEN = ("requires_payment_method", "requires_action")
PAID = ("succeeded", "partially_refunded", "refunded")


def serialize_intent(intent: PaymentIntent, include_secret: bool = False) -> dict:
    out = {
        "id": intent.id, "object": "payment_intent", "livemode": intent.mode == "live",
        "amount": intent.amount, "currency": intent.currency, "amount_refunded": intent.amount_refunded,
        "status": intent.status, "description": intent.description, "customer": intent.customer,
        "payment_methods": intent.allowed_methods, "metadata": intent.metadata_,
        "method": intent.method, "provider": intent.provider, "next_action": intent.next_action,
        "failure_reason": intent.failure_reason, "return_url": intent.return_url,
        "customer_id": intent.customer_id, "invoice": intent.invoice_id, "subscription": intent.subscription_id,
        "payment_link": intent.payment_link_id, "transfers": intent.transfers,
        "risk": {"score": intent.risk_score, "level": intent.risk_level, "reasons": intent.risk_reasons}
        if intent.risk_level else None,
        "created": iso(intent.created_at), "updated": iso(intent.updated_at),
    }
    if include_secret:
        out["client_secret"] = intent.client_secret
        out["checkout_url"] = checkout_url(intent)
    return out


def checkout_url(intent: PaymentIntent) -> str:
    # The secret travels in the URL fragment, which browsers never send to servers or logs.
    return f"{settings.base_url}/pay/{intent.id}#cs={intent.client_secret}"


def serialize_refund(r: Refund) -> dict:
    return {"id": r.id, "object": "refund", "payment_intent": r.intent_id, "amount": r.amount,
            "currency": r.currency, "status": r.status, "reason": r.reason,
            "bank_account": r.bank_account_id, "provider_ref": r.provider_ref, "created": iso(r.created_at)}


def create_intent(db: Session, merchant_id: str, mode: str, *, amount: int, currency: str,
                  description: str | None, customer: dict, payment_methods: list[str] | None = None,
                  return_url: str | None = None, metadata: dict | None = None, customer_id: str | None = None,
                  invoice_id: str | None = None, subscription_id: str | None = None,
                  payment_link_id: str | None = None, transfers: list[dict] | None = None) -> PaymentIntent:
    currency = currency.upper()
    if payment_methods:
        unknown = [m for m in payment_methods if m not in METHODS]
        if unknown:
            raise APIError(400, "invalid_method", f"Unknown payment methods: {unknown}", "payment_methods")
    if transfers:
        if sum(int(t["amount"]) for t in transfers) > amount:
            raise APIError(400, "invalid_transfers", "Transfers add up to more than the payment amount", "transfers")
    intent_id = new_id("pi")
    intent = PaymentIntent(
        id=intent_id, merchant_id=merchant_id, mode=mode, amount=amount, currency=currency,
        description=description, customer=customer, allowed_methods=payment_methods, return_url=return_url,
        metadata_=metadata or {}, client_secret=f"{intent_id}_secret_{secrets.token_urlsafe(24)}",
        customer_id=customer_id, invoice_id=invoice_id, subscription_id=subscription_id,
        payment_link_id=payment_link_id, transfers=transfers)
    db.add(intent)
    db.flush()
    emit(db, intent.merchant_id, intent.mode, "payment_intent.created", serialize_intent(intent))
    return intent


def confirm(db: Session, intent: PaymentIntent, method: str, method_data: dict,
            client_ip: str | None = None) -> PaymentIntent:
    from .models import Merchant

    if intent.status not in OPEN:
        raise APIError(409, "invalid_state", f"Cannot confirm a payment in status '{intent.status}'")
    if intent.allowed_methods and method not in intent.allowed_methods:
        raise APIError(400, "method_not_allowed", f"'{method}' is not enabled for this payment", "method")
    merchant = db.get(Merchant, intent.merchant_id)
    try:
        risk.evaluate(db, merchant, intent, client_ip)
    except APIError:
        emit(db, intent.merchant_id, intent.mode, "payment_intent.blocked", serialize_intent(intent))
        db.commit()
        raise
    if intent.risk_level == "elevated":
        emit(db, intent.merchant_id, intent.mode, "payment_intent.risk_flagged", serialize_intent(intent))
    routes = routes_for(db, merchant, intent.mode, method, intent.currency)
    if not routes:
        raise APIError(400, "method_unavailable", f"No configured provider supports {method} in {intent.currency}",
                       "method")

    errors = []
    for route in routes:
        attempt = PaymentAttempt(id=new_id("pa"), intent_id=intent.id, provider=route.provider.name,
                                 provider_account_id=route.ctx.account_id, method=method, status="created", data={},
                                 client_ip=client_ip)
        db.add(attempt)
        db.flush()
        key = f"{intent.merchant_id}:{route.provider.name}"
        try:
            result = route.provider.create(route.ctx, intent, attempt, method_data)
        except ProviderError as exc:
            breaker.record(key, ok=False)
            record_outcome(db, intent.merchant_id, route.provider.name, method, ok=False)
            attempt.status, attempt.error = "failed", str(exc)[:500]
            errors.append(f"{route.provider.name}: {exc}")
            continue  # fail over: nothing was charged yet, so trying the next gateway is safe
        breaker.record(key, ok=True)
        attempt.provider_ref, attempt.status = result.provider_ref, "requires_action"
        attempt.data = {**attempt.data, **result.data}
        intent.method, intent.provider, intent.latest_attempt_id = method, route.provider.name, attempt.id
        intent.status, intent.next_action, intent.failure_reason = "requires_action", result.next_action, None
        if result.status != "requires_action":
            apply_status(db, intent, attempt, result.status)
        db.commit()
        return intent

    intent.failure_reason = "; ".join(errors)[:500]
    db.commit()
    raise APIError(502, "providers_unavailable", "Every provider for this method failed. " + intent.failure_reason)


def apply_status(db: Session, intent: PaymentIntent, attempt: PaymentAttempt, status: str,
                 failure_reason: str | None = None, data: dict | None = None) -> None:
    """Single place where gateway outcomes change our state. Safe to call repeatedly."""
    if data:
        attempt.data = {**attempt.data, **data}
        for k in ("stripe_payment_intent", "razorpay_payment_id", "paypal_capture_id"):
            if data.get(k):
                attempt.provider_payment_id = data[k]

    if status in ("succeeded", "failed") and attempt.status != status:
        record_outcome(db, intent.merchant_id, attempt.provider, attempt.method, ok=status == "succeeded")

    if status == "succeeded":
        if attempt.status == "succeeded":
            return
        if intent.status in PAID:
            # Customer paid twice (e.g. retried on another gateway after the first went through).
            attempt.status = "succeeded"
            emit(db, intent.merchant_id, intent.mode, "payment_intent.duplicate_payment",
                 {"payment_intent": intent.id, "attempt": attempt.id, "provider": attempt.provider,
                  "amount": intent.amount, "currency": intent.currency})
            return
        attempt.status = "succeeded"
        intent.status, intent.provider, intent.method = "succeeded", attempt.provider, attempt.method
        intent.latest_attempt_id, intent.next_action, intent.failure_reason = attempt.id, None, None
        db.flush()
        ledger.post_payment(db, intent)
        emit(db, intent.merchant_id, intent.mode, "payment_intent.succeeded", serialize_intent(intent))
        after_success(db, intent, attempt)
        return

    if status == "processing":
        attempt.status = "processing"
        if intent.latest_attempt_id == attempt.id and intent.status in OPEN + ("pending_verification",):
            intent.status, intent.next_action = "processing", None
            emit(db, intent.merchant_id, intent.mode, "payment_intent.processing", serialize_intent(intent))
        return

    if status == "failed":
        if attempt.status == "failed":
            return
        attempt.status, attempt.error = "failed", failure_reason
        if intent.latest_attempt_id == attempt.id and intent.status in OPEN + ("processing", "pending_verification"):
            intent.status, intent.next_action = "requires_payment_method", None
            intent.failure_reason = failure_reason or "Payment failed"
            emit(db, intent.merchant_id, intent.mode, "payment_intent.payment_failed", serialize_intent(intent))
            on_payment_failed_hook(db, intent)
        return

    attempt.status = status


def after_success(db: Session, intent: PaymentIntent, attempt: PaymentAttempt) -> None:
    """Fan-out once a payment is captured: invoices, subscriptions, payment links, marketplace splits."""
    from . import billing, marketplace

    billing.on_payment_succeeded(db, intent)
    if intent.transfers:
        marketplace.execute_transfers(db, intent, attempt)


def on_payment_failed_hook(db: Session, intent: PaymentIntent) -> None:
    from . import billing

    billing.on_payment_failed(db, intent)


def sync_attempt(db: Session, intent: PaymentIntent, attempt: PaymentAttempt, params: dict) -> None:
    provider = REGISTRY[attempt.provider]
    account = db.get(ProviderAccount, attempt.provider_account_id) if attempt.provider_account_id else None
    result = provider.sync(context_for(account, intent.mode), intent, attempt, params)
    apply_status(db, intent, attempt, result.status, result.failure_reason, result.data)
    db.commit()


_UTR = re.compile(r"^\d{12}$")


def submit_utr(db: Session, intent: PaymentIntent, utr: str) -> PaymentIntent:
    utr = utr.strip()
    if intent.provider != "upi_direct" or intent.status != "requires_action":
        raise APIError(409, "invalid_state", "This payment is not waiting for a UPI reference")
    if not _UTR.match(utr):
        raise APIError(400, "invalid_utr", "UPI reference (UTR/RRN) is the 12-digit number in your UPI app", "utr")
    reused = (db.query(PaymentAttempt).join(PaymentIntent, PaymentIntent.id == PaymentAttempt.intent_id)
              .filter(PaymentIntent.merchant_id == intent.merchant_id, PaymentAttempt.provider_ref == f"utr:{utr}")
              .first())
    if reused:
        raise APIError(409, "utr_already_used", "This UPI reference was already submitted for another payment", "utr")
    attempt = db.get(PaymentAttempt, intent.latest_attempt_id)
    attempt.provider_ref, attempt.status = f"utr:{utr}", "pending_verification"
    attempt.data = {**attempt.data, "utr": utr}
    intent.status, intent.next_action = "pending_verification", None
    emit(db, intent.merchant_id, intent.mode, "payment_intent.requires_verification",
         {**serialize_intent(intent), "utr": utr})
    db.commit()
    return intent


def verify_manual(db: Session, intent: PaymentIntent, approve: bool, note: str | None) -> PaymentIntent:
    if intent.status != "pending_verification":
        raise APIError(409, "invalid_state", "Only payments in pending_verification can be verified")
    attempt = db.get(PaymentAttempt, intent.latest_attempt_id)
    if approve:
        apply_status(db, intent, attempt, "succeeded", data={"verified_note": note})
    else:
        apply_status(db, intent, attempt, "failed", failure_reason=note or "Merchant could not find this UPI payment")
    db.commit()
    return intent


def cancel(db: Session, intent: PaymentIntent) -> PaymentIntent:
    if intent.status not in OPEN:
        raise APIError(409, "invalid_state", f"Cannot cancel a payment in status '{intent.status}'")
    intent.status, intent.next_action = "canceled", None
    emit(db, intent.merchant_id, intent.mode, "payment_intent.canceled", serialize_intent(intent))
    db.commit()
    return intent


def refund(db: Session, intent: PaymentIntent, amount: int | None, reason: str | None,
           bank_account_id: str | None) -> Refund:
    if intent.status not in ("succeeded", "partially_refunded"):
        raise APIError(409, "invalid_state", "Only succeeded payments can be refunded")
    remaining = intent.amount - intent.amount_refunded
    amount = remaining if amount is None else amount
    if amount <= 0 or amount > remaining:
        raise APIError(400, "invalid_amount", f"Refund amount must be between 1 and {remaining}", "amount")
    if bank_account_id:
        ba = db.get(BankAccount, bank_account_id)
        if ba is None or ba.merchant_id != intent.merchant_id or ba.mode != intent.mode:
            raise APIError(404, "not_found", "Bank account not found", "bank_account")

    attempt = db.get(PaymentAttempt, intent.latest_attempt_id)
    account = db.get(ProviderAccount, attempt.provider_account_id) if attempt.provider_account_id else None
    try:
        result = REGISTRY[attempt.provider].refund(context_for(account, intent.mode), intent, attempt, amount)
    except ProviderError as exc:
        raise APIError(502, "provider_error", str(exc)) from exc
    if result.status == "failed":
        raise APIError(502, "refund_failed", "The provider rejected the refund")

    r = Refund(id=new_id("rf"), merchant_id=intent.merchant_id, intent_id=intent.id, amount=amount,
               currency=intent.currency, status=result.status, reason=reason, bank_account_id=bank_account_id,
               provider_ref=result.provider_ref)
    db.add(r)
    intent.amount_refunded += amount
    intent.status = "refunded" if intent.amount_refunded == intent.amount else "partially_refunded"
    db.flush()
    ledger.post_refund(db, intent, r.id, amount)
    emit(db, intent.merchant_id, intent.mode, "refund.created", serialize_refund(r))
    db.commit()
    return r


def complete_manual_refund(db: Session, r: Refund, reference: str | None) -> Refund:
    if r.status != "manual_action_required":
        raise APIError(409, "invalid_state", "Only manual refunds can be marked completed")
    r.status, r.provider_ref = "succeeded", reference or r.provider_ref
    intent = db.get(PaymentIntent, r.intent_id)
    emit(db, r.merchant_id, intent.mode, "refund.succeeded", serialize_refund(r))
    db.commit()
    return r
