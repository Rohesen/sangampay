# Contributing to SangamPay

Thanks for helping build open payment infrastructure! 🙏

## Getting set up

```bash
git clone https://github.com/Rohesen/sangampay.git && cd sangampay
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
uvicorn sangam.main:app --reload
python -m sangam.demo        # in a second terminal, for demo data
```

## Good first contributions

- **A new gateway adapter** (Cashfree, PayU, PhonePe PG, Juspay, Adyen, Mollie, Pix, M-Pesa…): subclass `Provider`
  in `sangam/providers/`, implement `create`, `sync`, `refund` and `handle_webhook`, register it in
  `providers/__init__.py`, and add tests with mocked HTTP.
- **Bank-statement formats** for reconciliation: add a sample CSV (with fake data) and a test in `tests/`.
- **Translations** of the checkout page.
- **Docs and examples** for frameworks (Django, Laravel, Next.js, Rails…).

## Ground rules

- Every behaviour change needs a test. `pytest -q` must pass.
- Payment state may only change in `payments.apply_status`. Never trust browser redirects for outcomes.
- Never log or return secrets, full bank numbers or card data.
- Keep the code readable: match the surrounding style, keep comments for the *why*.
- Security issues go to [SECURITY.md](SECURITY.md), not public issues.

## Pull requests

1. Fork and create a branch: `git checkout -b feat/cashfree-adapter`.
2. Make the change with tests.
3. Open a PR describing what changed, why, and how you tested it (screenshots for UI changes).

By contributing you agree your work is released under the [MIT License](LICENSE).
