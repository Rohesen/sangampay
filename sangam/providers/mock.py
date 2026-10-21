"""Sandbox provider: every method, no credentials, test mode only.

method_data.simulate controls the outcome without a browser:
  "success" | "fail"  -> finish immediately
  "provider_down"     -> raise, so you can watch routing fail over
  (absent)            -> redirect to a hosted approve/decline page
"""
from __future__ import annotations

from .base import (CreateResult, MandateResult, Provider, ProviderContext, ProviderError, RefundResult,
                   SyncResult)


class MockProvider(Provider):
    name = "mock"
    display_name = "Sandbox"
    methods = {"upi", "netbanking", "card", "bank_debit", "paypal"}
    test_only = True
    needs_account = False

    def create(self, ctx: ProviderContext, intent, attempt, method_data) -> CreateResult:
        simulate = method_data.get("simulate")
        if simulate == "provider_down":
            raise ProviderError("mock: simulated outage")
        ref = f"mock_{attempt.id[-12:]}"
        if simulate in ("success", "fail"):
            status = "succeeded" if simulate == "success" else "failed"
            return CreateResult(provider_ref=ref, next_action={"type": "none"}, status=status)
        return CreateResult(provider_ref=ref, next_action={
            "type": "redirect", "url": f"{ctx.base_url}/sandbox/mock/{attempt.id}"})

    def sync(self, ctx, intent, attempt, params) -> SyncResult:
        decision = attempt.data.get("decision")
        if decision == "approve":
            return SyncResult(status="succeeded")
        if decision == "decline":
            return SyncResult(status="failed", failure_reason="Declined in sandbox")
        if params.get("canceled"):
            return SyncResult(status="failed", failure_reason="Customer canceled")
        return SyncResult(status=attempt.status)

    def refund(self, ctx, intent, attempt, amount) -> RefundResult:
        return RefundResult(status="succeeded", provider_ref=f"mockrf_{attempt.id[-8:]}")

    # Recurring: we run the schedule ourselves and "debit" the sandbox mandate.
    # Set subscription metadata {"simulate": "fail"} to watch dunning retries.
    mandate_methods = {"upi", "emandate", "card", "bank_debit"}
    supports_transfers = True
    supports_payouts = True

    def create_mandate(self, ctx, sub, plan, customer, return_url) -> MandateResult:
        return MandateResult(provider_ref=f"mockmd_{sub.id[-12:]}",
                             authorization_url=f"{ctx.base_url}/sandbox/mandate/{sub.id}")

    def sync_mandate(self, ctx, sub, params) -> str:
        return {"approve": "active", "decline": "revoked"}.get(params.get("decision"), sub.mandate_status or "pending")

    def charge_mandate(self, ctx, sub, intent) -> tuple[str, str | None]:
        if (sub.metadata_ or {}).get("simulate") == "fail":
            return "failed", None
        return "succeeded", f"mockchg_{intent.id[-10:]}"

    def transfer(self, ctx, intent, attempt, destination, amount) -> str:
        return f"mocktr_{destination[-6:]}_{amount}"

    def payout(self, ctx, payout, bank) -> tuple[str, str]:
        if payout.narration == "simulate_failure":
            return "failed", f"mockpo_{payout.id[-10:]}"
        return "processed", f"mockpo_{payout.id[-10:]}"
