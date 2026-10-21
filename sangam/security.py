"""API key auth and webhook signatures."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey, Merchant
from .util import APIError, utcnow


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_secret_key(mode: str) -> tuple[str, str]:
    raw = f"sk_{mode}_{secrets.token_urlsafe(32)}"
    return raw, f"{raw[:12]}…{raw[-4:]}"


ROLES = ("admin", "developer", "support", "viewer")
# Every role can read. Writes need a scope; admin has all of them.
ROLE_SCOPES: dict[str, set[str]] = {
    "admin": {"*"},
    "developer": {"payments", "refunds", "billing", "settings", "webhooks", "vault", "recon", "disputes"},
    "support": {"payments", "refunds", "disputes", "recon"},
    "viewer": set(),
}


@dataclass
class Auth:
    merchant: Merchant
    mode: str
    key: ApiKey

    def can(self, scope: str) -> bool:
        scopes = ROLE_SCOPES.get(self.key.role, set())
        return "*" in scopes or scope in scopes


def require_secret_key(request: Request, authorization: str | None = Header(default=None),
                       db: Session = Depends(get_db)) -> Auth:
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(401, "unauthorized", "Send your secret key as 'Authorization: Bearer sk_...'")
    key = db.query(ApiKey).filter_by(key_hash=hash_key(authorization[7:].strip()), revoked=False).first()
    if key is None:
        raise APIError(401, "unauthorized", "Invalid or revoked API key")
    now = utcnow()
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
        key.last_used_at = now
        db.commit()
    auth = Auth(merchant=db.get(Merchant, key.merchant_id), mode=key.mode, key=key)
    request.state.auth = auth  # picked up by the audit-log middleware
    return auth


def scoped(scope: str):
    """Dependency for write endpoints: the key's role must grant `scope`."""
    def dep(auth: Auth = Depends(require_secret_key)) -> Auth:
        if not auth.can(scope):
            raise APIError(403, "insufficient_permissions",
                           f"This key's role '{auth.key.role}' cannot perform '{scope}' actions")
        return auth
    return dep


def check_client_secret(expected: str, given: str | None) -> None:
    if not given or not hmac.compare_digest(expected, given):
        raise APIError(401, "unauthorized", "Invalid client secret")


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Header value for outgoing webhooks: 't=<unix>,v1=<hex hmac-sha256 of 't.body'>'."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    """Reference implementation merchants can copy to verify Sangam webhooks."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))
