from .base import Provider, ProviderContext, ProviderError, WebhookSignatureError
from .mock import MockProvider
from .paypal import PayPalProvider
from .razorpay import RazorpayProvider
from .razorpayx import RazorpayXProvider
from .stripe import StripeProvider
from .upi_direct import UpiDirectProvider

REGISTRY: dict[str, Provider] = {p.name: p for p in (
    UpiDirectProvider(), RazorpayProvider(), StripeProvider(), PayPalProvider(), RazorpayXProvider(),
    MockProvider())}

METHODS = {
    "upi": "UPI (GPay, PhonePe, Paytm, BHIM...)",
    "netbanking": "Net banking (pay from your bank account)",
    "card": "Credit / debit card",
    "bank_debit": "Bank account debit (ACH / SEPA / Bacs / BECS)",
    "paypal": "PayPal",
}

# Methods a customer can authorise for automatic recurring debits.
MANDATE_METHODS = {
    "upi": "UPI AutoPay",
    "emandate": "e-NACH bank mandate",
    "card": "Card (RBI e-mandate compliant)",
    "bank_debit": "ACH / SEPA / Bacs debit",
}

# Default provider order per method; merchants can override per method.
DEFAULT_ROUTES = {
    "upi": ["razorpay", "upi_direct"],
    "netbanking": ["razorpay"],
    "card": ["stripe", "razorpay"],
    "bank_debit": ["stripe"],
    "paypal": ["paypal"],
}

__all__ = ["REGISTRY", "METHODS", "MANDATE_METHODS", "DEFAULT_ROUTES", "Provider", "ProviderContext",
           "ProviderError", "WebhookSignatureError"]
