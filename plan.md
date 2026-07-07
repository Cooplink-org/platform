# Cooplink — MVP system design & build plan

Cooplink: a marketplace where developers list GitHub projects for sale, other developers buy and download them, sellers see sales/stats on a dashboard, and payout happens manually by an admin after a 7-day freeze. Payment via MirPay.uz.

This doc has three parts: the assumptions/decisions made for MVP, the gaps that still need real-world answers, and a phase-by-phase set of prompts to feed your agentic coding AI, one at a time.

---

## 1. Decisions made for MVP (flag anything you want changed)

- **Stack**: Django + Django REST Framework + PostgreSQL + Celery/Redis. Matches what you already know, and API-first means any frontend (Django templates, a separate React app, mobile later) can sit on top without a rewrite.
- **Code delivery**: buyers download a **zipped snapshot** of the repo taken at publish time — not a live GitHub repo transfer or collaborator access. Reasons: a seller renaming/deleting/privating their repo later can't break a sale, buyers never see the seller's other repos or full commit history, and revoking access on refund is trivial (just block the download endpoint) instead of needing GitHub API calls to un-invite a collaborator.
- **Escrow accounting**: "frozen" vs "available" is a computed property based on timestamps (`sale_date + 7 days`), not a status flag flipped by a cron job. Simpler, always correct even if a background job doesn't run that day.
- **Listings require manual admin approval** before going live — code here gets downloaded and run by strangers, so this is a basic malware/plagiarism/quality gate, not just a nice-to-have.
- **Platform fee % is snapshotted per order** at sale time, so changing the fee later doesn't rewrite history.
- **Card numbers for payout are encrypted at rest**, masked everywhere except the one admin payout screen. Manual payout means Cooplink never touches money going out programmatically — the admin transfers it by hand and just marks the request done.
- **Package/env management: `uv`** instead of pip/venv — `pyproject.toml` + `uv.lock` for reproducible installs, `uv run` for everything.
- **Admin panel: Django's built-in admin, themed with `django-unfold`**, instead of a hand-rolled DRF admin API. Listing review, payout processing, transaction ledger, and user management all become customized `ModelAdmin` classes with Unfold's dashboard/actions — far less code than a bespoke API + frontend, and it's an internal tool so it doesn't need to match the public site's design.

---

## 2. MirPay integration — now confirmed

Full picture, once you shared the rest of the docs:

- `POST /api/connect?kassaid=...&api_key=...` → returns the Bearer token used everywhere else. `kassaid`/`api_key` come from the MirPay.uz dashboard, not from us — store them as env vars, cache the resulting token (don't re-fetch it on every request), and refresh on a 401.
- `POST /api/create-pay?summa=...&info_pay=...` → starts a payment. We pass our internal order reference through `info_pay` (e.g. `"Buyurtma ID: {order.id}"`), which is what should come back in the webhook's `comment` field.
- `POST /api/pay/invoice/` (form field `payid`) → **checks the real status of a payment on demand.** This is the piece that matters most: since the webhook body carries no signature, we never trust it on its own — every webhook triggers a `check_status(payid)` call, and only that independent answer decides whether an order gets marked paid. That turns an unverifiable webhook into a solid flow.
- `GET /api/balans` → balance, staff-only visibility.
- Still open: the docs excerpt doesn't show the exact JSON shape `create-pay` returns (payid, link, etc. — field names to be confirmed against a real test call), and whether the connect token expires. Phase 5 below handles both defensively (logs the raw response, refreshes the token on auth failure) rather than assuming a shape that turns out wrong.

### Legal basics still open
Nothing yet handles: a seller confirming they actually own the code they're listing, what license a buyer gets, or what happens on a dispute/refund — especially one that lands after a payout already went out. Phase 10 adds a minimum-viable version of each.

---

## 3. Core data model (v1)

`User` (+github_id, is_seller) · `Project` (listing) · `ProjectSnapshot` (the actual zip sold) · `Category` · `Order` · `Transaction` (ledger: sale_earning / platform_fee / refund / payout) · `PayoutRequest` · `WebhookLog`.

---

## 4. Phase-by-phase prompts

Copy each block into your agentic coding AI **one at a time, in order**. Each one assumes everything from the previous phases already exists.

### Phase 0 — Project skeleton

```
You are building the backend for Cooplink, a marketplace where developers list GitHub projects for sale and other developers buy and download them. Stack: Python 3.12, Django 5.x, Django REST Framework, PostgreSQL, Celery + Redis for background jobs. Use `uv` for all package/environment management — no pip/venv, no requirements.txt.

Set up:
- Initialize the project with `uv init` / `uv add`, producing a `pyproject.toml` and `uv.lock`. All commands run via `uv run` (e.g. `uv run manage.py runserver`).
- A new Django project named `cooplink` with these apps: `accounts`, `listings`, `orders`, `payments`, `payouts`, `dashboard`.
- Install and configure `django-unfold` for the Django admin (this becomes the internal staff tool — listing review, payouts, ledger — built out in later phases). Unfold must be added to INSTALLED_APPS *before* `django.contrib.admin`. Get the default Unfold-themed admin loading at /admin/ with a placeholder site title "Cooplink Admin".
- `.env`-based settings split (base/dev/prod) using django-environ.
- PostgreSQL connection via env vars.
- DRF installed with JWT auth scaffolding (djangorestframework-simplejwt).
- Celery configured with Redis broker, a basic debug_task to confirm it runs.
- CORS configured (django-cors-headers).
- A health-check endpoint GET /api/health/.
- README with `uv`-based setup instructions, docker-compose.yml for local Postgres+Redis.

This phase is only the skeleton — confirm it runs with `uv run manage.py runserver`, the health check responds, and /admin/ loads with the Unfold theme. No business logic yet.
```

### Phase 1 — Accounts & GitHub OAuth (login only)

```
Add a custom User model in the `accounts` app (extend AbstractUser) with: github_id, github_username, avatar_url, bio, is_seller (bool), created_at.

Implement GitHub OAuth2 login:
- GET /api/auth/github/login/ redirects to GitHub's authorize URL with scope `read:user user:email` only — do NOT request repo access here, that's a separate incremental step later.
- GET /api/auth/github/callback/ exchanges the code for a token, fetches the GitHub profile, creates/updates the User, issues our own JWT, and does not persist the GitHub token beyond this request.
- GET /api/auth/me/ returns the current user's profile.
- PATCH /api/auth/me/ lets the user edit bio/avatar.

Store the GitHub OAuth client id/secret in env vars. Add a README note on registering a GitHub OAuth App and the required callback URL. Write tests for the callback flow with a mocked GitHub API response.
```

### Phase 2 — Becoming a seller & listing creation

```
Add an incremental-auth flow so buyers aren't forced through repo permissions at signup:
- GET /api/auth/github/connect-repos/ starts a second OAuth request with scope `public_repo` (MVP: public repos only — note in a comment that private repos with `repo` scope can come later), storing the resulting token **encrypted** (Fernet, key from env) on the User, and setting is_seller=True.
- GET /api/listings/my-repos/ — authenticated seller endpoint, calls the GitHub API with their stored token, returns repo name, description, default_branch, private flag, updated_at, size.

In the `listings` app, add a Project model: seller (FK), title, slug, description, github_repo_full_name, github_default_branch, price (Decimal, UZS), category (FK), tags, cover_image, screenshots (JSON list), demo_url, tech_stack, license_type, status (draft/pending_review/published/rejected/suspended), version (int, default 1), created_at, updated_at.

Add CRUD endpoints for a seller's own Projects. Status starts at draft. A separate POST /api/listings/{id}/submit/ moves draft → pending_review. Once pending_review or published, block direct edits (explain in a comment: we snapshot at publish time, so silent edits shouldn't change what buyers already paid for — a new version cycle is the only path to changing a published listing).
```

### Phase 3 — Snapshot pipeline & admin review

```
Add a ProjectSnapshot model in `listings`: project (FK), version (int), storage_path, file_size, commit_sha, created_at.

Add a Celery task create_project_snapshot(project_id) that:
1. Uses the seller's encrypted GitHub token to call GET /repos/{owner}/{repo}/tarball/{ref}.
2. Streams the tarball into object storage (django-storages, S3-compatible; local dev falls back to FileSystemStorage in a private, non-served directory).
3. Creates a ProjectSnapshot row pointing at the stored file with the current commit SHA.

Trigger this automatically when a seller calls POST /api/listings/{id}/submit/ (moves to pending_review and kicks off the snapshot task in the background). If snapshot creation fails, revert status to draft and surface the error to the seller.

Register Project in the Django admin with a custom ModelAdmin (Unfold-styled):
- list_display: title, seller, status, price, created_at.
- list_filter: status, category.
- A default queryset/filter view for the review queue (status=pending_review) — use Unfold's list filters or a saved filter link so staff land on the queue quickly.
- Two admin actions: "Approve selected" (status → published) and a per-object "Reject" action that prompts for a reason (Unfold supports action forms; a simple intermediate confirmation page with a reason field is fine) and stores it on the Project, setting status → rejected.
- Show the linked ProjectSnapshot(s) inline (read-only) on the Project admin page so staff can see what was actually captured.

Snapshot files must never be publicly reachable by URL — access only through an authenticated download view built in Phase 5.
```

### Phase 4 — Public marketplace browsing

```
Build the public catalog in `listings`:
- GET /api/listings/ — published projects only, paginated, filterable by category/tags/price range/tech_stack, sortable by newest/price/popularity (a view_count field incremented on detail view).
- GET /api/listings/{slug}/ — detail view: title, description, screenshots, demo_url, tech_stack, price, seller's public profile. Never expose github_repo_full_name or private repo details to non-purchasers.
- A Category model (name, slug), seeded via data migration with a handful of starter categories (e.g. Telegram bots, e-commerce, automation scripts, web apps, mobile apps, APIs & backends).
- Basic search via a `?q=` param using Postgres icontains/full-text search on title/description — no need for Elasticsearch at this stage.
```

### Phase 5 — Orders & MirPay payment

```
Build `orders` and `payments` apps.

orders.Order: buyer (FK), project (FK), seller (denormalized FK), price_at_purchase, platform_fee_percent (snapshotted), platform_fee_amount, seller_earning_amount, status (pending_payment/paid/failed/refunded), payment_ref (MirPay payid, nullable), created_at, paid_at.

payments app — build a MirPayClient wrapping the real MirPay.uz API:
- Store `kassaid` and `api_key` as env vars (issued via the MirPay.uz dashboard — never hardcode them).
- get_token(): POST https://mirpay.uz/api/connect?kassaid={kassaid}&api_key={api_key} to obtain a Bearer token. Cache it (Redis is fine) instead of re-requesting on every call; on a 401 from any other MirPay call, transparently re-fetch a fresh token and retry once.
- create_payment(order): POST https://mirpay.uz/api/create-pay?summa={amount}&info_pay={reference}, Bearer auth, where reference is something like `"Buyurtma ID: {order.id}"`. Log the raw response and extract whatever payment id/link it contains onto the Order — since the exact response field names aren't confirmed yet from the docs, write this defensively (don't assume a shape; fail loudly with the raw body logged if parsing doesn't find what's expected) and leave a TODO to lock the field names down after one real test call.
- check_status(payid): POST https://mirpay.uz/api/pay/invoice/ with form field payid, Bearer auth. Returns MirPay's own authoritative status for that payment — this is the trust anchor, since the webhook payload itself has no signature.

Two webhook endpoints, POST /api/payments/mirpay/webhook/success/ and POST /api/payments/mirpay/webhook/fail/ (registered separately in MirPay's kassa settings):
- Parse the incoming form-encoded payid, summa, status, comment, chek, fiskal, sana.
- Look up the Order via the comment field (should match the reference passed as info_pay at creation).
- Never trust the webhook body alone: immediately call check_status(payid) and only proceed if that independent response confirms success, and summa matches order.price_at_purchase exactly.
- Ignore gracefully (idempotent, return 200) if the Order isn't currently pending_payment, to handle replayed/duplicate webhook calls safely.
- On confirmed success: mark Order paid, set paid_at, create the ledger Transactions from Phase 6, grant download access.
- On confirmed fail, or if the independent status check disagrees with the webhook: mark Order failed and log the mismatch clearly.
- Log every raw webhook payload AND its matching check_status response to a WebhookLog model (endpoint, raw_body, verification_response, received_at, matched_order), for audits.

GET /api/payments/mirpay/balance/ (staff-only) — thin wrapper around GET /api/balans.

Build GET /api/orders/{id}/download/ — authenticated, checks the requester is the buyer on a paid Order, streams the latest ProjectSnapshot for that project from private storage (signed/expiring URL if supported, otherwise a Django view streaming the file directly).
```

### Phase 6 — Escrow ledger & payouts

```
Add a Transaction model (orders app or a new `ledger` app): user (FK), order (FK, nullable), type (sale_earning/platform_fee/refund/payout), amount, created_at.

On order paid (from Phase 5), create one sale_earning Transaction for the seller and one platform_fee Transaction.

Add a SellerBalance helper (computed live from Transactions, not stored):
- available_balance(user): sum of sale_earning transactions older than 7 days, minus payouts, minus refunds.
- pending_balance(user): sum of sale_earning transactions newer than 7 days, each exposing an unlocks_at (created_at + 7 days) for a per-sale countdown on the dashboard.

Build the `payouts` app:
- PayoutRequest: seller (FK), amount, destination_card_encrypted, destination_card_last4, status (requested/processing/completed/rejected), admin_note, requested_at, processed_at, processed_by (FK, staff).
- POST /api/payouts/request/ — seller submits amount (validate ≤ available_balance) and card number (encrypt before storing, never return the full number in any response afterward, only last4).
- GET /api/payouts/mine/ — seller's own payout history.

Register PayoutRequest in the Django admin (Unfold-styled), staff-only by default:
- list_display: seller, amount, destination_card_last4, status, requested_at — never show the decrypted card number in list view, only last4.
- list_filter: status.
- Admin actions: "Mark as processing", "Complete payout" (creates the payout Transaction, sets processed_at and processed_by to the acting admin user), "Reject" (with a reason field).
- The full decrypted card number should only be visible on the individual object's detail page, and only to staff — add a comment noting this is the one place it's ever decrypted for display.

Add a comment on the "Complete payout" action making clear the actual money movement happens manually by the admin outside this system (bank transfer/card-to-card) — this action only records that it happened.
```

### Phase 7 — Seller dashboard & stats

```
Build read-only aggregation endpoints in `dashboard`, all scoped strictly to request.user:
- GET /api/dashboard/summary/ — lifetime revenue, available_balance, pending_balance (with next unlock date), total sales, total published listings, total downloads.
- GET /api/dashboard/sales/ — paginated Orders for this seller: buyer username, project, amount, date, status.
- GET /api/dashboard/listings/ — this seller's Projects with view_count, sales_count, revenue per listing.
- GET /api/dashboard/earnings-timeseries/?range=30d — daily bucketed earnings for a simple chart.
```

### Phase 8 — Admin panel completion

```
Round out the Django admin (Unfold-themed), all staff-only by default:
- Register User with a custom ModelAdmin: list_display includes username, email, is_seller, is_active, is_staff, date_joined; list_filter on is_seller/is_active/is_staff; a search field; and a read-only inline or computed field showing a seller's lifetime sales count and revenue on their detail page.
- Register Transaction (read-only ModelAdmin — this is a ledger, nothing should be editable by hand) with list_display: user, type, amount, order, created_at, and list_filter on type and date.
- Register Order with list_display: buyer, project, seller, status, price_at_purchase, created_at; list_filter on status and date range; search by buyer/seller/project.
- Add a custom admin action on Order, "Refund selected", available only while the related seller earning is still within the 7-day frozen window (validate per-row; skip and report any rows outside that window with a message explaining a payout already went out and this needs a manual conversation with the seller instead). On success: reverses the sale_earning and platform_fee transactions with matching refund entries, marks Order refunded.
- Build a small custom Unfold dashboard view (Unfold supports custom dashboard callbacks/templates) showing basic platform metrics: GMV, active sellers, total published listings, this month vs last month.
```

### Phase 9 — Notifications

```
Add a lightweight notification layer using a Telegram bot (reusing the Pyrogram experience already on hand, rather than building email infra from scratch):
- A Celery task notify(user_id, event_type, context) fired on: listing approved/rejected, sale made (to seller), funds unlocked (to seller — a daily Celery beat job scanning for earnings that just crossed the 7-day mark), payout completed/rejected.
- Store an optional telegram_chat_id per user, set via a /link command in the bot after the user follows a deep link from their dashboard.
- If no telegram_chat_id is linked yet, just log the notification for now — no email fallback needed for MVP.
```

### Phase 10 — Hardening before launch

```
Do a pass focused on safety, not features:
- Confirm the MirPay webhook views enforce amount-matching, idempotency, and full logging as specced in Phase 5. Write tests simulating a replayed/spoofed webhook and prove it's rejected or safely ignored.
- Audit that GitHub tokens and card numbers are encrypted at rest — confirm they never appear in logs or admin list views in plaintext.
- Add rate limiting on auth endpoints, webhook endpoints, and the download endpoint (DRF throttling is fine for MVP).
- Add a seller-facing checkbox at listing submission: "I confirm I own or have the right to sell this code, and I agree to Cooplink's seller terms" — store the acceptance timestamp.
- Write a short SECURITY.md documenting what's still unconfirmed on the MirPay side: (1) the exact JSON field names `create-pay` returns, (2) whether the connect token expires and needs proactive refresh — small items, but worth not forgetting before going live with real money.
- A basic smoke test suite: signup→login, listing creation→admin approval, purchase→webhook→download, payout request→admin completion.
```

---

## How to use this

Run Phase 0 first, review what comes back, then move to Phase 1, and so on — don't hand over multiple phases at once, the agent will do better with one clear scope at a time. If a phase produces something that conflicts with a later one (e.g. you rename an app), just tell the agent about that state before pasting the next prompt.
