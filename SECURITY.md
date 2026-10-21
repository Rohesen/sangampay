# Security policy

Please report vulnerabilities privately through GitHub Security Advisories ("Report a vulnerability" on the
repository's Security tab), not in public issues. We aim to acknowledge within 72 hours and to ship a fix for
confirmed critical issues within 14 days.

## Design summary

- **Non-custodial.** SangamPay never holds funds; money settles in the merchant's own gateway and bank accounts.
- **API keys** are stored only as SHA-256 hashes, scoped to test or live mode, and carry a role
  (admin, developer, support, viewer). Every write is recorded in an audit log.
- **Vault.** Gateway credentials, webhook secrets and bank details use per-record AES-256-GCM envelope encryption
  with the owner bound as associated data. Bank details are write-only through the API and are decrypted only
  inside the payout call. Master keys rotate via `SANGAM_PREVIOUS_MASTER_KEYS`.
- **No sensitive entry.** Customers enter card numbers and bank logins on gateway or bank hosted pages.
- **Trustworthy outcomes.** Payment results come only from server-to-server gateway calls or signature-verified
  gateway webhooks, never from browser redirect parameters.
- **Webhooks** are HMAC-SHA256 signed with a timestamp (5-minute replay window). In production, webhook targets on
  private networks are refused (SSRF protection).
- **Abuse controls.** Per-key and per-IP rate limits, a fraud rules engine (blocklists, velocity, amount caps,
  country allow-list, risk scoring), and idempotency keys on money-moving endpoints.
- **Client secrets** for checkout travel in URL fragments, which browsers never send to servers, proxies or logs.

## Deployment checklist

- Serve only over HTTPS behind a reverse proxy; set `SANGAM_ENV=production` and `SANGAM_TRUST_PROXY=true`.
- Store `SANGAM_MASTER_KEY` in a secrets manager or KMS, and back it up separately from database backups.
- Keep `SANGAM_ALLOW_SIGNUP=false` except when creating your first merchant.
- Configure webhook secrets on every connected gateway.
- Take regular encrypted database backups and test restores.
