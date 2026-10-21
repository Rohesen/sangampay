"""Smart routing: pick the gateway for a method, skip unhealthy ones, fall over on failure."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import vault
from .config import settings
from .models import Merchant, ProviderAccount, ProviderStat
from .providers import DEFAULT_ROUTES, METHODS, REGISTRY, Provider, ProviderContext


class CircuitBreaker:
    """After `threshold` consecutive failures a provider is skipped for `cooldown` seconds."""

    def __init__(self, threshold: int = 5, cooldown: float = 60.0) -> None:
        self.threshold, self.cooldown = threshold, cooldown
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, key: str) -> bool:
        with self._lock:
            return self._open_until.get(key, 0) > time.monotonic()

    def record(self, key: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures.pop(key, None)
                self._open_until.pop(key, None)
                return
            self._failures[key] = self._failures.get(key, 0) + 1
            if self._failures[key] >= self.threshold:
                self._open_until[key] = time.monotonic() + self.cooldown
                self._failures[key] = 0


breaker = CircuitBreaker()


@dataclass
class Route:
    provider: Provider
    ctx: ProviderContext


def context_for(account: ProviderAccount | None, mode: str) -> ProviderContext:
    if account is None:
        return ProviderContext(account_id=None, credentials={}, config={}, mode=mode, base_url=settings.base_url)
    creds = vault.decrypt(account.enc_credentials, f"provider:{account.merchant_id}:{account.id}") \
        if account.enc_credentials else {}
    return ProviderContext(account_id=account.id, credentials=creds, config=account.config or {},
                           mode=mode, base_url=settings.base_url)


def routes_for(db: Session, merchant: Merchant, mode: str, method: str, currency: str) -> list[Route]:
    accounts = {a.provider: a for a in db.query(ProviderAccount)
                .filter_by(merchant_id=merchant.id, mode=mode, enabled=True)}
    order = list((merchant.routing or {}).get(method) or DEFAULT_ROUTES.get(method, []))
    if mode == "test" and "mock" not in order:
        order.append("mock")  # sandbox is always the last resort in test mode
    names = []
    for name in order:
        provider = REGISTRY.get(name)
        if provider is None or not provider.supports(method, currency):
            continue
        if provider.test_only and mode != "test":
            continue
        if provider.needs_account and name not in accounts:
            continue
        if breaker.is_open(f"{merchant.id}:{name}"):
            continue
        names.append(name)
    names = rank(db, merchant, method, names)
    return [Route(REGISTRY[n], context_for(accounts.get(n), mode)) for n in names]


# ---- Strategy ranking -----------------------------------------------------------------------

# Typical Indian/global pricing (percent of amount) used by the "cost" strategy; override per merchant
# in settings.fees, e.g. {"razorpay": 1.75}.
DEFAULT_FEES = {"upi_direct": 0.0, "razorpay": 2.0, "stripe": 2.9, "paypal": 4.4, "mock": 99.0}
DECAY = 0.98  # each new outcome weighs ~2% more than the one before: tracks the last ~50 attempts


def success_score(stat: ProviderStat | None) -> float:
    """Bayesian success rate with a weak 50% prior, so new gateways still get traffic."""
    if stat is None:
        return 0.5
    return (stat.successes + 1) / (stat.attempts + 2)


def rank(db: Session, merchant: Merchant, method: str, names: list[str]) -> list[str]:
    strategy = merchant.routing_strategy or "priority"
    sandbox = [n for n in names if n == "mock"]
    real = [n for n in names if n != "mock"]
    if strategy == "success_rate":
        stats = {s.provider: s for s in db.query(ProviderStat).filter_by(merchant_id=merchant.id, method=method)}
        real.sort(key=lambda n: -success_score(stats.get(n)))  # stable: ties keep merchant priority
    elif strategy == "cost":
        fees = {**DEFAULT_FEES, **((merchant.settings or {}).get("fees") or {})}
        real.sort(key=lambda n: fees.get(n, 3.0))
    return real + sandbox


def record_outcome(db: Session, merchant_id: str, provider: str, method: str, ok: bool) -> None:
    stat = db.get(ProviderStat, (merchant_id, provider, method))
    if stat is None:
        stat = ProviderStat(merchant_id=merchant_id, provider=provider, method=method, attempts=0.0, successes=0.0)
        db.add(stat)
    stat.attempts = stat.attempts * DECAY + 1
    stat.successes = stat.successes * DECAY + (1 if ok else 0)


def provider_health(db: Session, merchant: Merchant) -> list[dict]:
    rows = db.query(ProviderStat).filter_by(merchant_id=merchant.id).all()
    return [{"provider": s.provider, "method": s.method, "success_rate": round(success_score(s) * 100, 1),
             "recent_attempts": round(s.attempts, 1),
             "circuit_open": breaker.is_open(f"{merchant.id}:{s.provider}")} for s in rows]


def available_methods(db: Session, merchant: Merchant, mode: str, currency: str,
                      allowed: list[str] | None = None) -> list[dict]:
    out = []
    for method, label in METHODS.items():
        if allowed and method not in allowed:
            continue
        routes = routes_for(db, merchant, mode, method, currency)
        if routes:
            out.append({"method": method, "label": label, "providers": [r.provider.name for r in routes]})
    return out
