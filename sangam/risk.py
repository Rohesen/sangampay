"""Fraud and risk engine, evaluated every time a payment is confirmed.

Hard rules (blocklists, country rules, amount caps, velocity limits) block outright. Soft signals add
to a 0-100 score; above `review_score` the payment proceeds but is flagged, above `block_score` it is
blocked. Every decision is stored on the payment with human-readable reasons."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Merchant, PaymentAttempt, PaymentIntent
from .util import APIError, utcnow

DEFAULT_RULES = {
    "max_amount": None,              # minor units; payments above are blocked
    "block_emails": [],              # exact emails or "@domain.com"
    "block_ips": [],
    "allowed_countries": [],         # empty = all
    "max_attempts_per_email_hour": 10,
    "max_attempts_per_ip_hour": 20,
    "review_score": 65,
    "block_score": 90,
}


def rules_for(merchant: Merchant) -> dict:
    return {**DEFAULT_RULES, **(merchant.risk_rules or {})}


def _block(intent: PaymentIntent, reason: str) -> None:
    intent.risk_score, intent.risk_level, intent.risk_reasons = 100, "blocked", [reason]
    raise APIError(402, "payment_blocked", f"This payment was blocked by risk rules: {reason}")


def evaluate(db: Session, merchant: Merchant, intent: PaymentIntent, ip: str | None) -> None:
    """Raises APIError(402) when blocked; otherwise stores score/level/reasons on the intent."""
    r = rules_for(merchant)
    email = (intent.customer or {}).get("email", "").lower()
    country = (intent.customer or {}).get("country")
    hour_ago = utcnow() - timedelta(hours=1)

    if email and any(email == b.lower() or (b.startswith("@") and email.endswith(b.lower()))
                     for b in r["block_emails"]):
        _block(intent, "customer email is on the blocklist")
    if ip and ip in r["block_ips"]:
        _block(intent, "IP address is on the blocklist")
    if r["allowed_countries"] and country and country not in r["allowed_countries"]:
        _block(intent, f"payments from {country} are not accepted")
    if r["max_amount"] and intent.amount > int(r["max_amount"]):
        _block(intent, "amount is above the configured maximum")

    attempts_q = (db.query(PaymentAttempt).join(PaymentIntent, PaymentIntent.id == PaymentAttempt.intent_id)
                  .filter(PaymentIntent.merchant_id == merchant.id, PaymentAttempt.created_at >= hour_ago))
    if ip:
        n_ip = attempts_q.filter(PaymentAttempt.client_ip == ip).count()
        if n_ip >= r["max_attempts_per_ip_hour"]:
            _block(intent, f"too many attempts from this IP ({n_ip} in the last hour)")

    score, reasons = 0, []
    if email:
        email_attempts = [a for a, i in (
            db.query(PaymentAttempt, PaymentIntent).join(PaymentIntent, PaymentIntent.id == PaymentAttempt.intent_id)
            .filter(PaymentIntent.merchant_id == merchant.id, PaymentAttempt.created_at >= hour_ago).all())
            if (i.customer or {}).get("email", "").lower() == email]
        if len(email_attempts) >= r["max_attempts_per_email_hour"]:
            _block(intent, f"too many attempts for this customer ({len(email_attempts)} in the last hour)")
        failures = sum(1 for a in email_attempts if a.status == "failed")
        if failures >= 3:
            score += 40
            reasons.append(f"{failures} failed attempts by this customer in the last hour")
    else:
        score += 10
        reasons.append("no customer email")

    avg = (db.query(func.avg(PaymentIntent.amount))
           .filter(PaymentIntent.merchant_id == merchant.id, PaymentIntent.mode == intent.mode,
                   PaymentIntent.currency == intent.currency, PaymentIntent.status == "succeeded").scalar())
    if avg and intent.amount > 5 * float(avg):
        score += 30
        reasons.append("amount is over 5x this merchant's average payment")
    if ip:
        n_ip = attempts_q.filter(PaymentAttempt.client_ip == ip).count()
        if n_ip >= max(r["max_attempts_per_ip_hour"] // 2, 3):
            score += 25
            reasons.append(f"{n_ip} attempts from this IP in the last hour")

    score = min(score, 100)
    intent.risk_score, intent.risk_reasons = score, reasons
    if score >= r["block_score"]:
        _block(intent, "; ".join(reasons) or "risk score too high")
    intent.risk_level = "elevated" if score >= r["review_score"] else "normal"
