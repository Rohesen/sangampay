"""Business analytics and CSV exports."""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from .billing import monthly_amount
from .models import Invoice, PaymentAttempt, PaymentIntent, Plan, Refund, Subscription
from .util import iso, to_major, utcnow

PAID = ("succeeded", "partially_refunded", "refunded")


def overview(db: Session, merchant_id: str, mode: str, days: int = 30, currency: str | None = None) -> dict:
    since = utcnow() - timedelta(days=days)
    intents = (db.query(PaymentIntent).filter(PaymentIntent.merchant_id == merchant_id, PaymentIntent.mode == mode,
                                              PaymentIntent.created_at >= since).all())
    currencies = Counter(i.currency for i in intents if i.status in PAID)
    currency = currency or (currencies.most_common(1)[0][0] if currencies else "INR")
    paid = [i for i in intents if i.status in PAID and i.currency == currency]

    series = defaultdict(lambda: {"volume": 0, "count": 0})
    for i in paid:
        day = i.created_at.date().isoformat()
        series[day]["volume"] += i.amount
        series[day]["count"] += 1
    today = utcnow().date()
    daily = [{"date": (today - timedelta(days=d)).isoformat(), **series[(today - timedelta(days=d)).isoformat()]}
             for d in range(days - 1, -1, -1)]

    attempts = (db.query(PaymentAttempt).join(PaymentIntent, PaymentIntent.id == PaymentAttempt.intent_id)
                .filter(PaymentIntent.merchant_id == merchant_id, PaymentIntent.mode == mode,
                        PaymentAttempt.created_at >= since, PaymentAttempt.status.in_(("succeeded", "failed"))).all())

    def rates(key):
        agg = defaultdict(lambda: [0, 0])
        for a in attempts:
            agg[key(a)][0] += 1
            agg[key(a)][1] += a.status == "succeeded"
        return [{"name": k, "attempts": n, "success_rate": round(ok * 100 / n, 1)} for k, (n, ok) in
                sorted(agg.items(), key=lambda kv: -kv[1][0])]

    failures = Counter((a.error or "Declined / abandoned").split(":")[0][:80] for a in attempts if a.status == "failed")
    refunds = (db.query(Refund).join(PaymentIntent, PaymentIntent.id == Refund.intent_id)
               .filter(Refund.merchant_id == merchant_id, PaymentIntent.mode == mode, Refund.created_at >= since,
                       Refund.currency == currency).all())
    subs = (db.query(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)
            .filter(Subscription.merchant_id == merchant_id, Subscription.mode == mode,
                    Subscription.status.in_(("active", "past_due")), Plan.currency == currency).all())
    churned = (db.query(Subscription).filter(Subscription.merchant_id == merchant_id, Subscription.mode == mode,
                                             Subscription.status == "canceled", Subscription.canceled_at >= since).count())
    open_invoices = (db.query(Invoice).filter_by(merchant_id=merchant_id, mode=mode, status="open", currency=currency).all())
    customers = {(i.customer or {}).get("email") for i in paid if (i.customer or {}).get("email")}
    total_attempts = len(attempts)
    succeeded = sum(1 for a in attempts if a.status == "succeeded")
    gross = sum(i.amount for i in paid)

    return {
        "currency": currency, "days": days,
        "gross_volume": gross, "payments": len(paid),
        "average_payment": gross // len(paid) if paid else 0,
        "refunded": sum(r.amount for r in refunds),
        "success_rate": round(succeeded * 100 / total_attempts, 1) if total_attempts else None,
        "unique_customers": len(customers),
        "daily": daily,
        "by_method": rates(lambda a: a.method),
        "by_provider": rates(lambda a: a.provider),
        "top_failure_reasons": [{"reason": r, "count": c} for r, c in failures.most_common(5)],
        "mrr": round(sum(monthly_amount(p, s.quantity) for s, p in subs)),
        "active_subscriptions": len(subs),
        "churned_subscriptions": churned,
        "outstanding_invoices": {"count": len(open_invoices), "amount": sum(i.total for i in open_invoices)},
        "risk_flagged": sum(1 for i in intents if i.risk_level in ("elevated", "blocked")),
    }


def export_csv(db: Session, merchant_id: str, mode: str, kind: str) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    if kind == "payments":
        w.writerow(["id", "created", "status", "amount", "currency", "amount_refunded", "method", "provider",
                    "customer_email", "description", "invoice", "risk_level"])
        for i in (db.query(PaymentIntent).filter_by(merchant_id=merchant_id, mode=mode)
                  .order_by(PaymentIntent.created_at.desc()).limit(100_000)):
            w.writerow([i.id, iso(i.created_at), i.status, to_major(i.amount, i.currency), i.currency,
                        to_major(i.amount_refunded, i.currency), i.method, i.provider, (i.customer or {}).get("email"),
                        i.description, i.invoice_id, i.risk_level])
    elif kind == "refunds":
        w.writerow(["id", "created", "payment_intent", "status", "amount", "currency", "reason"])
        for r in (db.query(Refund).join(PaymentIntent, PaymentIntent.id == Refund.intent_id)
                  .filter(Refund.merchant_id == merchant_id, PaymentIntent.mode == mode)
                  .order_by(Refund.created_at.desc()).limit(100_000)):
            w.writerow([r.id, iso(r.created_at), r.intent_id, r.status, to_major(r.amount, r.currency), r.currency, r.reason])
    elif kind == "invoices":
        w.writerow(["number", "id", "created", "status", "customer", "currency", "subtotal", "tax", "total",
                    "tax_breakdown", "paid_at"])
        for inv in (db.query(Invoice).filter_by(merchant_id=merchant_id, mode=mode)
                    .order_by(Invoice.created_at.desc()).limit(100_000)):
            w.writerow([inv.number, inv.id, iso(inv.created_at), inv.status, inv.customer_id, inv.currency,
                        to_major(inv.subtotal, inv.currency), to_major(inv.tax_total, inv.currency),
                        to_major(inv.total, inv.currency),
                        "; ".join(f"{b['name']}={to_major(b['amount'], inv.currency)}" for b in inv.tax_breakdown),
                        iso(inv.paid_at)])
    else:
        raise ValueError(kind)
    return out.getvalue()
