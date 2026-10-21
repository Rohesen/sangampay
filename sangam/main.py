from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from . import api, api_billing, api_ops, checkout, hosted, ratelimit
from .config import settings
from .db import SessionLocal, init_db
from .models import AuditLog
from .util import APIError
from .workers import start_workers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = threading.Event()
    if settings.run_workers:
        start_workers(stop)
    yield
    stop.set()


app = FastAPI(title="SangamPay", version="1.0.0", lifespan=lifespan,
              description="Open-source, self-hosted payment orchestration: UPI, net banking, cards, "
                          "bank debits and PayPal behind one API, with subscriptions, GST invoices, "
                          "payment links, reconciliation, payouts and fraud rules. Money settles directly "
                          "into your own accounts.")
app.include_router(api.router)
app.include_router(api_billing.router)
app.include_router(api_ops.router)
app.include_router(checkout.router)
app.include_router(hosted.router)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(exc.to_dict(), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    param = ".".join(str(p) for p in first["loc"] if p != "body")
    return JSONResponse({"error": {"code": "invalid_request", "message": first["msg"], "param": param}},
                        status_code=400)


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def audit_log(request: Request, call_next):
    """Records every write made with an API key: who, what, when, from where, and the result."""
    response = await call_next(request)
    auth = getattr(request.state, "auth", None)
    if auth is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
        with SessionLocal() as db:
            db.add(AuditLog(merchant_id=auth.merchant.id, key_id=auth.key.id, actor=auth.key.name, mode=auth.mode,
                            method=request.method, path=request.url.path[:300], status_code=response.status_code,
                            ip=client_ip(request)))
            db.commit()
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/v1/", "/l/", "/pay/", "/i/")):
        bucket, key = ratelimit.key_for(request.headers.get("authorization"), client_ip(request))
        wait = bucket.take(key)
        if wait:
            return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests, slow down."}},
                                status_code=429, headers={"Retry-After": str(max(1, int(wait + 0.999)))})
    request.state.client_ip = client_ip(request)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith("/sdk/"):
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}
