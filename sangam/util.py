"""Small shared helpers: ids, time, money formatting, errors."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal

_ALPHABET = string.ascii_letters + string.digits

# ISO 4217 currencies whose minor unit is not 2 decimal places.
_ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF",
                 "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
_THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}


def new_id(prefix: str, length: int = 24) -> str:
    return f"{prefix}_" + "".join(secrets.choice(_ALPHABET) for _ in range(length))


def utcnow() -> datetime:
    """Naive UTC, so values compare consistently on SQLite and Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


def currency_exponent(currency: str) -> int:
    currency = currency.upper()
    if currency in _ZERO_DECIMAL:
        return 0
    if currency in _THREE_DECIMAL:
        return 3
    return 2


def to_major(amount: int, currency: str) -> str:
    """Minor units (e.g. paise) -> decimal string (e.g. '499.00') as most gateways expect."""
    exp = currency_exponent(currency)
    value = Decimal(amount) / (Decimal(10) ** exp)
    return f"{value:.{exp}f}"


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.param = status, code, message, param

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.param:
            err["param"] = self.param
        return {"error": err}
