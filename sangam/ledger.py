"""Double-entry ledger. Sangam never holds funds; the ledger records what moved where so
merchants get one reconciled view across every gateway they use."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import LedgerEntry, PaymentIntent

REVENUE = "merchant:revenue"
REFUNDS = "merchant:refunds"


def _post(db: Session, intent: PaymentIntent, txn_id: str, lines: list[tuple[str, int]]) -> None:
    assert sum(amount for _, amount in lines) == 0, "ledger transaction must balance"
    if db.query(LedgerEntry.id).filter_by(txn_id=txn_id).first():
        return  # already posted; posting is idempotent
    for account, amount in lines:
        db.add(LedgerEntry(merchant_id=intent.merchant_id, mode=intent.mode, txn_id=txn_id,
                           account=account, currency=intent.currency, amount=amount))


def post_payment(db: Session, intent: PaymentIntent) -> None:
    clearing = f"clearing:{intent.provider}"
    _post(db, intent, f"pay:{intent.id}", [(clearing, intent.amount), (REVENUE, -intent.amount)])


def post_refund(db: Session, intent: PaymentIntent, refund_id: str, amount: int) -> None:
    clearing = f"clearing:{intent.provider}"
    _post(db, intent, f"refund:{refund_id}", [(REFUNDS, amount), (clearing, -amount)])


def balance(db: Session, merchant_id: str, mode: str) -> list[dict]:
    rows = (db.query(LedgerEntry.currency, LedgerEntry.account, func.sum(LedgerEntry.amount))
            .filter_by(merchant_id=merchant_id, mode=mode)
            .group_by(LedgerEntry.currency, LedgerEntry.account).all())
    per_currency: dict[str, dict] = defaultdict(
        lambda: {"gross": 0, "refunded": 0, "disputed": 0, "paid_out": 0, "by_provider": {}})
    for currency, account, total in rows:
        bucket = per_currency[currency]
        if account == REVENUE:
            bucket["gross"] = -int(total)
        elif account == REFUNDS:
            bucket["refunded"] = int(total)
        elif account == "merchant:disputes":
            bucket["disputed"] = int(total)
        elif account == "payouts:sent":
            bucket["paid_out"] = int(total)
        elif account.startswith("clearing:"):
            bucket["by_provider"][account.split(":", 1)[1]] = int(total)
    return [{"currency": c, "gross_volume": b["gross"], "refunded": b["refunded"], "disputes_lost": b["disputed"],
             "net_volume": b["gross"] - b["refunded"] - b["disputed"], "payouts_sent": b["paid_out"],
             "by_provider": b["by_provider"]}
            for c, b in sorted(per_currency.items())]
