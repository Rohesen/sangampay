"""Automatic reconciliation of zero-fee UPI payments against bank statements.

Feed it your bank statement (CSV export from net banking, or JSON rows from a bank API / account
aggregator) and it matches credits to payments by UTR and amount, then confirms them for you:

  * payment waiting for verification + UTR and amount match  -> approved automatically
  * customer paid but never typed the UTR, and the bank narration contains the payment ID (we send
    it as the UPI `tr` reference)                              -> marked paid automatically
  * anything ambiguous is left for a human, with the reason
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from . import payments
from .models import PaymentAttempt, PaymentIntent, ReconRun
from .util import APIError, currency_exponent, new_id
from .webhooks import emit

UTR = re.compile(r"(?<!\d)(\d{12})(?!\d)")
PI_REF = re.compile(r"pi_[A-Za-z0-9]{24}")

# Column names differ by bank ("Deposit Amt.", "Credit", "CR Amount", "Chq./Ref.No.", "Transaction Remarks"...),
# so columns are recognised by keywords rather than exact names.
CREDIT_WORDS = ("deposit", "credit", "cr amount")
TEXT_WORDS = ("narration", "description", "particulars", "remarks", "details")
REF_WORDS = ("ref", "utr", "rrn", "transaction id")
TYPE_COLS = ("type", "cr/dr", "dr/cr")


def _cols(row: dict, words: tuple) -> list[str]:
    return [k for k in row if any(w in k for w in words)]


def _money(value: str, currency: str) -> int | None:
    cleaned = re.sub(r"[^\d.\-]", "", value or "")
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return int((amount * 10 ** currency_exponent(currency)).to_integral_value()) if amount > 0 else None


def parse_csv(text: str, currency: str = "INR") -> list[dict]:
    """Bank exports often have a few lines of account info before the table: find the header row."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:40])
                  if any(w in line.lower() for w in TEXT_WORDS)
                  and any(w in line.lower() for w in ("credit", "deposit", "amount"))), 0)
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
        typ = next((row[c] for c in TYPE_COLS if c in row), "").upper()
        credit = None
        for col in _cols(row, CREDIT_WORDS):
            credit = _money(row[col], currency)
            if credit:
                break
        if credit is None and "amount" in row and typ.startswith("C"):  # single amount column + CR/DR flag
            credit = _money(row["amount"], currency)
        if not credit:
            continue
        text_parts = [row[c] for c in _cols(row, TEXT_WORDS) + _cols(row, REF_WORDS) if row[c]]
        rows.append({"amount": credit, "text": " ".join(text_parts)})
    return rows


def reconcile(db: Session, merchant_id: str, mode: str, rows: list[dict], source: str,
              auto_approve: bool = True) -> ReconRun:
    """rows: [{"amount": minor units, "text": narration + references}]"""
    if len(rows) > 20000:
        raise APIError(400, "too_many_rows", "Upload at most 20,000 statement rows at a time")
    matched, report = 0, {"matched": [], "unmatched": [], "needs_review": []}

    pending = {a.provider_ref[4:]: (a, i) for a, i in (
        db.query(PaymentAttempt, PaymentIntent).join(PaymentIntent, PaymentIntent.id == PaymentAttempt.intent_id)
        .filter(PaymentIntent.merchant_id == merchant_id, PaymentIntent.mode == mode,
                PaymentIntent.status == "pending_verification", PaymentAttempt.provider_ref.like("utr:%")).all())}

    for row in rows:
        text, amount = row.get("text", ""), int(row["amount"])
        hit = None
        for utr in UTR.findall(text):
            if utr in pending:
                attempt, intent = pending[utr]
                if intent.amount == amount:
                    hit = ("utr", utr, attempt, intent)
                else:
                    report["needs_review"].append({"utr": utr, "payment_intent": intent.id,
                                                   "reason": f"amount mismatch: statement {amount}, expected {intent.amount}"})
                break
        if hit is None:
            for ref in PI_REF.findall(text):
                intent = db.get(PaymentIntent, ref)
                if (intent and intent.merchant_id == merchant_id and intent.mode == mode
                        and intent.provider == "upi_direct" and intent.status in ("requires_action", "pending_verification")):
                    if intent.amount == amount:
                        hit = ("reference", ref, db.get(PaymentAttempt, intent.latest_attempt_id), intent)
                    else:
                        report["needs_review"].append({"payment_intent": ref, "reason": "amount mismatch"})
                    break
        if hit is None:
            report["unmatched"].append({"amount": amount, "text": text[:140]})
            continue
        how, ref, attempt, intent = hit
        if auto_approve:
            utr = UTR.search(text)
            payments.apply_status(db, intent, attempt, "succeeded",
                                  data={"reconciled_by": how, "statement_utr": utr.group(1) if utr else None})
            pending.pop(ref, None)
        matched += 1
        report["matched"].append({"payment_intent": intent.id, "amount": amount, "matched_by": how, "ref": ref})

    run = ReconRun(id=new_id("rc"), merchant_id=merchant_id, mode=mode, source=source, rows=len(rows), matched=matched,
                   report=report)
    db.add(run)
    emit(db, merchant_id, mode, "reconciliation.completed",
         {"id": run.id, "rows": run.rows, "matched": matched, "unmatched": len(report["unmatched"]),
          "needs_review": len(report["needs_review"])})
    db.commit()
    return run
