from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .util import utcnow


class Merchant(Base):
    __tablename__ = "merchants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    country: Mapped[str] = mapped_column(String(2), default="IN")
    # Per-method provider order, e.g. {"upi": ["upi_direct", "razorpay"]}. Empty = defaults.
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
    routing_strategy: Mapped[str] = mapped_column(String(16), default="priority")  # priority|success_rate|cost
    # Business profile for invoices: legal_name, gstin, state_code, address, support_email, invoice_prefix
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    # Misc settings: fees {provider: pct}, invoice_due_days, dunning_days, auto_reconcile
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))  # test | live
    name: Mapped[str] = mapped_column(String(80), default="Default key")
    role: Mapped[str] = mapped_column(String(16), default="admin")  # admin|developer|support|viewer
    display: Mapped[str] = mapped_column(String(40))  # sk_test_ab…yz, safe to show
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256, raw key never stored
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProviderAccount(Base):
    """A merchant's own gateway account. Money settles straight into it (non-custodial)."""
    __tablename__ = "provider_accounts"
    __table_args__ = (UniqueConstraint("merchant_id", "provider", "mode"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(4))
    enc_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)  # vault envelope
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret settings
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    __table_args__ = (Index("ix_pi_merchant_created", "merchant_id", "mode", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"))
    mode: Mapped[str] = mapped_column(String(4))
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units
    currency: Mapped[str] = mapped_column(String(3))
    amount_refunded: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="requires_payment_method")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    customer: Mapped[dict] = mapped_column(JSON, default=dict)
    allowed_methods: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    return_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    client_secret: Mapped[str] = mapped_column(String(80))
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latest_attempt_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    invoice_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payment_link_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transfers: Mapped[list | None] = mapped_column(JSON, nullable=True)  # marketplace splits
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PaymentAttempt(Base):
    """One try at one provider. Smart routing may create several per intent."""
    __tablename__ = "payment_attempts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_account_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    method: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))  # created|requires_action|processing|succeeded|failed
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # Gateway's payment/charge id (Stripe PaymentIntent, Razorpay payment, PayPal capture) for disputes/transfers.
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))  # succeeded|pending|manual_action_required|failed
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bank_account_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LedgerEntry(Base):
    """Double-entry ledger. Every txn_id sums to zero; (txn_id, account) is unique so posting is idempotent."""
    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("txn_id", "account"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    txn_id: Mapped[str] = mapped_column(String(80))
    account: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[int] = mapped_column(BigInteger)  # + debit, - credit
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BankAccount(Base):
    """Encrypted bank details (merchant settlement accounts, customer refund accounts).
    Write-only through the API: only the masked view is ever returned."""
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("merchant_id", "mode", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    owner: Mapped[str] = mapped_column(String(16))  # merchant | customer
    customer_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # in_bank | iban | us_ach | upi_vpa
    enc_data: Mapped[str] = mapped_column(Text)
    masked: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    url: Mapped[str] = mapped_column(String(2000))
    enc_secret: Mapped[str] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_merchant_created", "merchant_id", "mode", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(4))
    type: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_deliveries_due", "status", "next_attempt_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"))
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|succeeded|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    merchant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- Billing ------------------------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)  # GST state code, e.g. 27
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    name: Mapped[str] = mapped_column(String(120))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    interval: Mapped[str] = mapped_column(String(8))  # day|week|month|year
    interval_count: Mapped[int] = mapped_column(Integer, default=1)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    tax_rate: Mapped[float] = mapped_column(default=0.0)  # percent, e.g. 18 for GST
    hsn_sac: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_refs: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"razorpay": "plan_X"}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # incomplete | trialing | active | past_due | unpaid | canceled
    status: Mapped[str] = mapped_column(String(16), index=True)
    collection: Mapped[str] = mapped_column(String(12))  # mandate | invoice
    mandate_method: Mapped[str | None] = mapped_column(String(16), nullable=True)  # upi | emandate | card
    mandate_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mandate_account_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mandate_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    mandate_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pending|active|revoked
    authorization_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("merchant_id", "mode", "number"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    access_token: Mapped[str] = mapped_column(String(64), unique=True)  # hosted invoice URL secret
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # draft|open|paid|void|uncollectible
    currency: Mapped[str] = mapped_column(String(3))
    line_items: Mapped[list] = mapped_column(JSON)
    subtotal: Mapped[int] = mapped_column(BigInteger)
    tax_total: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    tax_breakdown: Mapped[list] = mapped_column(JSON, default=list)  # [{"name": "CGST 9%", "amount": ..}]
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaymentLink(Base):
    __tablename__ = "payment_links"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None = customer chooses
    min_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    payment_methods: Mapped[list | None] = mapped_column(JSON, nullable=True)
    max_payments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payments_count: Mapped[int] = mapped_column(Integer, default=0)
    amount_collected: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- Operations ---------------------------------------------------------------------------

class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # needs_response|under_review|won|lost
    provider: Mapped[str] = mapped_column(String(32))
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    due_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    bank_account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    transfer_mode: Mapped[str] = mapped_column(String(8))  # IMPS|NEFT|RTGS|UPI
    purpose: Mapped[str] = mapped_column(String(32))
    narration: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # processing|processed|failed|reversed
    provider: Mapped[str] = mapped_column(String(32))
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Transfer(Base):
    """Marketplace split: part of a payment routed to a linked/connected account."""
    __tablename__ = "transfers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    destination: Mapped[str] = mapped_column(String(120))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16))
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProviderStat(Base):
    """Exponentially decayed success counters per gateway+method, used for success-rate routing."""
    __tablename__ = "provider_stats"
    merchant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    method: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempts: Mapped[float] = mapped_column(default=0.0)
    successes: Mapped[float] = mapped_column(default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_merchant_created", "merchant_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(40))
    key_id: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(4))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    status_code: Mapped[int] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconRun(Base):
    __tablename__ = "recon_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(4))
    source: Mapped[str] = mapped_column(String(32))  # csv | api
    rows: Mapped[int] = mapped_column(Integer)
    matched: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
