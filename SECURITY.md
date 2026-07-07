# Cooplink Security & Audit

This document outlines the security posture and technical guards implemented in the Cooplink marketplace.

## 1. Financial Integrity (The Ledger)
- **Immutable Ledger**: All movements of funds are recorded in the `orders.Transaction` model. 
- **Read-Only Ledger**: The Django admin is configured to prevent many-to-many edits, deletions, or manual creation of ledger entries.
- **Computed Balances**: Seller balances are calculated live from the ledger (`SALE_EARNING` - `REFUND` - `PAYOUT`). No "balance" field exists on the User model, preventing desync.

## 2. Escrow & Fraud Prevention
- **7-Day Freeze**: All earnings are frozen for 7 days before becoming available for payout. This covers the typical dispute widow.
- **Refund Logic**: Refunds are only automated within this 7-day window. If funds have already been paid out, the system blocks automatic refunds and requires manual staff intervention.

## 3. Data Protection
- **Encryption at Rest**: Sensitive data (GitHub tokens, Payout card numbers) is encrypted using AES-128 via Fernet.
- **Token Masking**: Admin views mask sensitive tokens. Full card numbers are only decryptable in the admin detail view for staff with processing perms.
- **No Card Storage**: We do not store card CVVs or expiry dates. Only primary account numbers for payout routing are stored (encrypted).

## 4. API & Infrastructure
- **Throttling**: DRF throttling is enabled for all endpoints to prevent brute-force attacks on auth and abuse of the download system.
- **Webhook Security**: MirPay webhooks are validated by an immediate follow-up status check via the MirPay API to ensure the payload was not spoofed.
- **Private Repo Protection**: GitHub integration is scoped to specific repositories. The system never exposes the full `github_repo_full_name` or private metadata to non-buyers.

## 5. Audit Logging
- **Payout Approval**: Every payout transition is stamped with `processed_by` (staff user) and `processed_at`.
## 6. MirPay Implementation Gaps (Pending Real-World Verification)

As real-world testing with the live MirPay API progresses, the following items must be confirmed and locked down:

1.  **JSON Schema Mismatch**: The exact JSON field names returned by `create-pay` (e.g., `payid` vs `PayId`, `link` vs `url`) are currently handled defensively. These should be hardcoded once a real response is captured.
2.  **Auth Token Expiry**: The duration of the `Bearer` token from `/api/connect` is unconfirmed. The system currently uses an "on-failure" refresh strategy (re-fetching upon receiving a 401). A proactive scheduled refresh should be implemented if the token lifetime is short.
3.  **Webhook Signature**: Confirm if MirPay has plans to implement HMAC signatures for webhooks. For now, the system relies on independent status verification (`check_status`) for every incoming webhook.
