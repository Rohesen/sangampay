"""The adapter contract every payment rail implements.

Adding a gateway (Cashfree, PayU, Adyen, Mollie, M-Pesa, Pix...) means writing one subclass
and registering it in providers/__init__.py. Nothing else in the codebase changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models import PaymentAttempt, PaymentIntent


class ProviderError(Exception):
    """Raised when a gateway call fails. At creation time, routing falls over to the next provider."""


class WebhookSignatureError(Exception):
    pass


@dataclass
class ProviderContext:
    account_id: str | None
    credentials: dict
    config: dict
    mode: str
    base_url: str

    def return_url(self, intent: PaymentIntent, attempt: PaymentAttempt) -> str:
        return f"{self.base_url}/pay/{intent.id}/return?attempt={attempt.id}"

    def cancel_url(self, intent: PaymentIntent, attempt: PaymentAttempt) -> str:
        return f"{self.base_url}/pay/{intent.id}/return?attempt={attempt.id}&canceled=1"

    def mandate_return_url(self, sub) -> str:
        return f"{self.base_url}/subscribe/{sub.id}/return"


@dataclass
class CreateResult:
    provider_ref: str
    next_action: dict
    status: str = "requires_action"
    data: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    status: str  # requires_action | processing | succeeded | failed
    failure_reason: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    status: str  # succeeded | pending | manual_action_required | failed
    provider_ref: str | None = None


@dataclass
class WebhookResult:
    """What an inbound gateway webhook means for us.

    kind: payment | dispute | mandate | subscription_charge | subscription_failed | payout
    provider_ref: the id we stored (session/order/link id, dispute id, subscription id, payout id)
    """
    provider_ref: str
    status: str
    kind: str = "payment"
    data: dict = field(default_factory=dict)


@dataclass
class MandateResult:
    provider_ref: str
    authorization_url: str


class Provider:
    name: str = ""
    display_name: str = ""
    methods: set[str] = set()
    currencies: set[str] | None = None  # None = any
    credential_fields: list[str] = []
    config_fields: list[str] = []
    test_only: bool = False
    needs_account: bool = True
    # Recurring payments. mandate_methods: which methods can be authorised for auto-debit.
    # managed_billing=True: the gateway runs the schedule and tells us about charges by webhook.
    mandate_methods: set[str] = set()
    managed_billing: bool = False
    supports_transfers: bool = False
    supports_payouts: bool = False

    def create_mandate(self, ctx: ProviderContext, sub, plan, customer, return_url: str) -> MandateResult:
        raise ProviderError(f"{self.name} does not support recurring payments")

    def sync_mandate(self, ctx: ProviderContext, sub, params: dict) -> str:
        """Returns pending | active | revoked."""
        return sub.mandate_status or "pending"

    def charge_mandate(self, ctx: ProviderContext, sub, intent) -> tuple[str, str | None]:
        """Only for non-managed providers: debit an authorised mandate. Returns (status, provider_ref)."""
        raise ProviderError(f"{self.name} charges mandates itself")

    def cancel_mandate(self, ctx: ProviderContext, sub) -> None:
        return None

    def transfer(self, ctx: ProviderContext, intent, attempt, destination: str, amount: int) -> str:
        raise ProviderError(f"{self.name} does not support split payments")

    def payout(self, ctx: ProviderContext, payout, bank: dict) -> tuple[str, str]:
        raise ProviderError(f"{self.name} does not support payouts")

    def submit_dispute_evidence(self, ctx: ProviderContext, dispute, evidence: dict) -> None:
        return None

    def supports(self, method: str, currency: str) -> bool:
        return method in self.methods and (self.currencies is None or currency in self.currencies)

    def validate_setup(self, credentials: dict, config: dict) -> None:
        missing = [f for f in self.credential_fields if not credentials.get(f)]
        missing += [f for f in self.config_fields if not config.get(f)]
        if missing:
            from ..util import APIError
            raise APIError(400, "missing_field", f"{self.name} needs: {', '.join(missing)}", missing[0])

    def create(self, ctx: ProviderContext, intent: PaymentIntent, attempt: PaymentAttempt,
               method_data: dict) -> CreateResult:
        raise NotImplementedError

    def sync(self, ctx: ProviderContext, intent: PaymentIntent, attempt: PaymentAttempt,
             params: dict) -> SyncResult:
        """Ask the gateway for the authoritative status (called on redirect return)."""
        return SyncResult(status=attempt.status)

    def refund(self, ctx: ProviderContext, intent: PaymentIntent, attempt: PaymentAttempt,
               amount: int) -> RefundResult:
        raise NotImplementedError

    def handle_webhook(self, ctx: ProviderContext, headers: dict, body: bytes) -> WebhookResult | None:
        return None

    # ---- HTTP helper -------------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        try:
            resp = httpx.request(method, url, timeout=20, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name} returned HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}
