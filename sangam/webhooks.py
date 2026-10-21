"""Events and signed, retried outgoing webhooks."""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from . import vault
from .config import settings
from .models import Event, WebhookDelivery, WebhookEndpoint
from .security import sign
from .util import APIError, iso, new_id, utcnow

log = logging.getLogger("sangam.webhooks")
MAX_ATTEMPTS = 8  # backoff: 30s, 1m, 2m, 4m ... ~1h total


def emit(db: Session, merchant_id: str, mode: str, type_: str, data: dict) -> Event:
    event = Event(id=new_id("evt"), merchant_id=merchant_id, mode=mode, type=type_, data=data)
    db.add(event)
    for ep in db.query(WebhookEndpoint).filter_by(merchant_id=merchant_id, mode=mode, enabled=True):
        if "*" in ep.events or type_ in ep.events:
            db.add(WebhookDelivery(id=new_id("whd"), endpoint_id=ep.id, event_id=event.id))
    return event


def event_payload(event: Event) -> dict:
    return {"id": event.id, "type": event.type, "livemode": event.mode == "live",
            "created": iso(event.created_at), "data": event.data}


def validate_url(url: str) -> None:
    """Block webhook targets on private networks in production (SSRF protection)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise APIError(400, "invalid_url", "Webhook URL must be an absolute http(s) URL", "url")
    if not settings.is_production:
        return
    if parsed.scheme != "https":
        raise APIError(400, "invalid_url", "Webhook URL must use https in production", "url")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise APIError(400, "invalid_url", "Webhook host does not resolve", "url") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise APIError(400, "invalid_url", "Webhook URL points to a private network", "url")


def deliver(db: Session, delivery: WebhookDelivery, client: httpx.Client) -> None:
    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    event = db.get(Event, delivery.event_id)
    secret = vault.decrypt(endpoint.enc_secret, f"webhook:{endpoint.merchant_id}:{endpoint.id}")["secret"]
    body = json.dumps(event_payload(event), separators=(",", ":")).encode()
    delivery.attempts += 1
    try:
        resp = client.post(endpoint.url, content=body, headers={
            "Content-Type": "application/json", "Sangam-Signature": sign(secret, body),
            "Sangam-Event-Id": event.id, "User-Agent": "SangamPay-Webhooks/1.0"})
        delivery.response_code = resp.status_code
        ok = 200 <= resp.status_code < 300
        delivery.last_error = None if ok else f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        ok = False
        delivery.last_error = f"{exc.__class__.__name__}: {exc}"[:500]
    if ok:
        delivery.status = "succeeded"
    elif delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
    else:
        delivery.next_attempt_at = utcnow() + timedelta(seconds=30 * 2 ** (delivery.attempts - 1))


def dispatch_due(db: Session, client: httpx.Client | None = None, limit: int = 50) -> int:
    own_client = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        due = (db.query(WebhookDelivery).filter(WebhookDelivery.status == "pending",
                                                WebhookDelivery.next_attempt_at <= utcnow())
               .order_by(WebhookDelivery.next_attempt_at).limit(limit).all())
        for d in due:
            deliver(db, d, client)
            db.commit()
        return len(due)
    finally:
        if own_client:
            client.close()


