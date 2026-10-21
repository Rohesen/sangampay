"""Customer-facing routes: hosted checkout, its client API (auth = per-payment client secret),
gateway redirect returns, inbound gateway webhooks, and the sandbox approval page."""
from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import payments
from .db import get_db
from .models import Merchant, PaymentAttempt, PaymentIntent, ProviderAccount
from .providers import REGISTRY, ProviderError, WebhookSignatureError
from .routing import available_methods, context_for
from .security import check_client_secret
from .util import APIError, to_major

log = logging.getLogger("sangam.checkout")
router = APIRouter()
STATIC = Path(__file__).parent / "static"


def _intent_for_client(db: Session, intent_id: str, client_secret: str | None) -> PaymentIntent:
    intent = db.get(PaymentIntent, intent_id)
    if intent is None:
        raise APIError(404, "not_found", "Payment not found")
    check_client_secret(intent.client_secret, client_secret)
    return intent


def _public_view(db: Session, intent: PaymentIntent) -> dict:
    merchant = db.get(Merchant, intent.merchant_id)
    view = {k: v for k, v in payments.serialize_intent(intent).items()
            if k not in ("metadata", "customer", "return_url")}
    view["merchant_name"] = merchant.name
    view["display_amount"] = to_major(intent.amount, intent.currency)
    view["available_methods"] = available_methods(db, merchant, intent.mode, intent.currency, intent.allowed_methods) \
        if intent.status in payments.OPEN else []
    return view


# ---- client API used by the hosted checkout & SDK -------------------------------------------

@router.get("/v1/checkout/{intent_id}")
def checkout_view(intent_id: str, x_client_secret: str | None = Header(default=None), db: Session = Depends(get_db)):
    return _public_view(db, _intent_for_client(db, intent_id, x_client_secret))


class ClientConfirmIn(BaseModel):
    method: str
    method_data: dict = {}


@router.post("/v1/checkout/{intent_id}/confirm")
def checkout_confirm(intent_id: str, body: ClientConfirmIn, request: Request,
                     x_client_secret: str | None = Header(default=None), db: Session = Depends(get_db)):
    intent = _intent_for_client(db, intent_id, x_client_secret)
    if intent.mode == "live":
        body.method_data.pop("simulate", None)
    payments.confirm(db, intent, body.method, body.method_data, client_ip=getattr(request.state, "client_ip", None))
    return _public_view(db, intent)


class UtrIn(BaseModel):
    utr: str = Field(max_length=32)


@router.post("/v1/checkout/{intent_id}/utr")
def checkout_utr(intent_id: str, body: UtrIn, x_client_secret: str | None = Header(default=None),
                 db: Session = Depends(get_db)):
    intent = payments.submit_utr(db, _intent_for_client(db, intent_id, x_client_secret), body.utr)
    return _public_view(db, intent)


# ---- hosted pages ---------------------------------------------------------------------------

@router.get("/pay/{intent_id}", include_in_schema=False)
def checkout_page(intent_id: str):
    return FileResponse(STATIC / "checkout.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/pay/{intent_id}/return", include_in_schema=False)
def gateway_return(intent_id: str, attempt: str, canceled: int = 0, db: Session = Depends(get_db)):
    """Gateways redirect the customer here. We never trust the query string for the outcome:
    we ask the gateway server-to-server (sync) and only then show the result."""
    intent = db.get(PaymentIntent, intent_id)
    att = db.get(PaymentAttempt, attempt)
    if intent is None or att is None or att.intent_id != intent.id:
        raise APIError(404, "not_found", "Payment not found")
    try:
        payments.sync_attempt(db, intent, att, {"canceled": bool(canceled)})
    except ProviderError:
        log.exception("sync failed for %s", att.id)  # webhook will settle it; show current state
    db.refresh(intent)
    if intent.return_url:
        sep = "&" if "?" in intent.return_url else "?"
        return RedirectResponse(f"{intent.return_url}{sep}{urlencode({'payment_intent': intent.id, 'status': intent.status})}",
                                status_code=303)
    return HTMLResponse(_result_page(db, intent))


def _result_page(db: Session, intent: PaymentIntent) -> str:
    merchant = db.get(Merchant, intent.merchant_id)
    ok = intent.status in ("succeeded", "processing")
    title = {"succeeded": "Payment successful", "processing": "Payment processing"}.get(intent.status, "Payment not completed")
    msg = (f"{intent.currency} {to_major(intent.amount, intent.currency)} paid to {merchant.name}." if intent.status == "succeeded"
           else "Your bank is processing this payment. You'll be notified when it clears." if intent.status == "processing"
           else intent.failure_reason or "You can go back and try another payment method.")
    color = "#0f7b4b" if ok else "#b42318"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f6f7f9;color:#14171f;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}}
.card{{background:#fff;border-radius:16px;padding:32px;max-width:420px;width:100%;box-shadow:0 4px 24px #0001;text-align:center}}
h1{{color:{color};font-size:22px}} code{{font-size:12px;color:#5b6170}}</style></head>
<body><div class="card"><h1>{html.escape(title)}</h1><p>{html.escape(msg)}</p><code>{html.escape(intent.id)}</code></div></body></html>"""


# ---- inbound gateway webhooks -------------------------------------------------------------

@router.post("/providers/{provider}/webhook/{account_id}", include_in_schema=False)
async def provider_webhook(provider: str, account_id: str, request: Request, db: Session = Depends(get_db)):
    impl = REGISTRY.get(provider)
    account = db.get(ProviderAccount, account_id)
    if impl is None or account is None or account.provider != provider:
        raise APIError(404, "not_found", "Unknown webhook target")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        result = impl.handle_webhook(context_for(account, account.mode), headers, body)
    except WebhookSignatureError as exc:
        raise APIError(400, "invalid_signature", str(exc)) from exc
    if result is None:
        return {"received": True, "handled": False}
    if result.kind != "payment":
        return {"received": True, "handled": _handle_non_payment(db, provider, account, result)}
    attempt = (db.query(PaymentAttempt).filter_by(provider=provider, provider_ref=result.provider_ref,
                                                  provider_account_id=account.id).first())
    if attempt is None:
        return {"received": True, "handled": False}
    intent = db.get(PaymentIntent, attempt.intent_id)
    if result.status == "succeeded" and provider in ("stripe", "paypal", "razorpay"):
        # Pull details we need for refunds (charge/capture ids) straight from the gateway.
        try:
            payments.sync_attempt(db, intent, attempt, {})
        except ProviderError:
            payments.apply_status(db, intent, attempt, "succeeded")
            db.commit()
    else:
        payments.apply_status(db, intent, attempt, result.status)
        db.commit()
    return {"received": True, "handled": True}


def _handle_non_payment(db: Session, provider: str, account: ProviderAccount, result) -> bool:
    from . import billing, marketplace
    from .models import Subscription

    if result.kind == "dispute":
        return marketplace.upsert_dispute(db, provider, result.provider_ref, result.status, result.data) is not None
    if result.kind == "payout":
        return marketplace.payout_webhook(db, result.provider_ref, result.status, result.data.get("reason"))

    sub_q = db.query(Subscription).filter_by(mandate_provider=provider, mandate_account_id=account.id)
    if result.kind == "mandate_session":  # Stripe: checkout session -> subscription id
        sub = sub_q.filter_by(mandate_ref=result.provider_ref).first()
        if sub and result.data.get("subscription"):
            sub.mandate_ref = result.data["subscription"]
            billing.mandate_authorized(db, sub, "active")
            db.commit()
        return sub is not None
    sub = sub_q.filter_by(mandate_ref=result.provider_ref).first()
    if sub is None:
        return False
    if result.kind == "mandate":
        billing.mandate_authorized(db, sub, result.status)
    elif result.kind == "subscription_charge":
        billing.record_managed_charge(db, sub, result.data.get("amount"), result.data.get("payment_id"))
    elif result.kind == "subscription_failed":
        billing.record_managed_failure(db, sub)
    db.commit()
    return True


# ---- sandbox ------------------------------------------------------------------------------

@router.get("/sandbox/mock/{attempt_id}", include_in_schema=False, response_class=HTMLResponse)
def sandbox_page(attempt_id: str, db: Session = Depends(get_db)):
    att = db.get(PaymentAttempt, attempt_id)
    if att is None or att.provider != "mock":
        raise APIError(404, "not_found", "Sandbox attempt not found")
    intent = db.get(PaymentIntent, att.intent_id)
    amount = html.escape(f"{intent.currency} {to_major(intent.amount, intent.currency)}")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sandbox bank</title>
<style>body{{font-family:system-ui,sans-serif;background:#fff8e6;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}}
.card{{background:#fff;border:2px dashed #d9a300;border-radius:16px;padding:28px;max-width:400px;width:100%;text-align:center}}
button{{font:inherit;padding:12px 20px;border-radius:10px;border:0;margin:6px;cursor:pointer}}
.ok{{background:#0f7b4b;color:#fff}}.no{{background:#eee}}</style></head><body><div class="card">
<p><b>TEST MODE</b> &middot; Sandbox {html.escape(att.method)}</p><h2>{amount}</h2>
<form method="post" action="/sandbox/mock/{html.escape(att.id)}/approve"><button class="ok">Approve payment</button></form>
<form method="post" action="/sandbox/mock/{html.escape(att.id)}/decline"><button class="no">Decline</button></form>
</div></body></html>"""


@router.post("/sandbox/mock/{attempt_id}/{decision}", include_in_schema=False)
def sandbox_decide(attempt_id: str, decision: str, db: Session = Depends(get_db)):
    att = db.get(PaymentAttempt, attempt_id)
    if att is None or att.provider != "mock" or decision not in ("approve", "decline"):
        raise APIError(404, "not_found", "Sandbox attempt not found")
    att.data = {**att.data, "decision": decision}
    db.commit()
    return RedirectResponse(f"/pay/{att.intent_id}/return?attempt={att.id}", status_code=303)


@router.get("/sdk/sangam.js", include_in_schema=False)
def sdk():
    return FileResponse(STATIC / "sangam.js", media_type="application/javascript")


ASSETS = {"dashboard.js": "application/javascript"}


@router.get("/assets/{name}", include_in_schema=False)
def asset(name: str):
    if name not in ASSETS:
        raise APIError(404, "not_found", "Asset not found")
    return FileResponse(STATIC / name, media_type=ASSETS[name], headers={"Cache-Control": "no-cache"})


@router.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html", headers={"Cache-Control": "no-store"})
