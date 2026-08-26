# Cooplink Backend

Cooplink is a marketplace where developers list GitHub projects for sale and other developers buy and download them. This repository hosts the Django-based backend.

## Tech Stack
* **Python**: 3.12
* **Framework**: Django 5.x + Django REST Framework (DRF)
* **Database**: PostgreSQL (fallback to SQLite for local development)
* **Background Processing**: Celery + Redis
* **Theme**: Django Unfold (Admin interface)
* **Environment Management**: `uv`

---

## Local Development Setup

### 1. Prerequisites
Ensure you have `uv` installed. If not, follow the [uv installation instructions](https://github.com/astral-sh/uv).

### 2. Install Dependencies
Sync project dependencies and setup virtual environment:
```bash
uv sync
```

### 3. Spin up Postgres & Redis
If you have Docker installed, spin up the local services defined in the `docker-compose.yml`:
```bash
docker compose up -d
```

### 4. Environment Variables Configuration
Copy the template environment variables:
```bash
cp .env.example .env
```
Ensure to review `.env` and set parameters like `SECRET_KEY`, `DATABASE_URL` and `CELERY_BROKER_URL`. If you don't run Postgres/Docker locally, commenting out `DATABASE_URL` will cause Django to fall back to a local SQLite database (`db.sqlite3`).

### 5. Apply Migrations
Run the migrations:
```bash
uv run manage.py migrate
```

### 6. Create Superuser (Admin User)
Create a superuser to access the Django Unfold-themed admin panel:
```bash
uv run manage.py createsuperuser
```

---

## Running the Application

### Start Development Server
```bash
uv run manage.py runserver
```
The server will start at [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

### Start Celery Worker
```bash
uv run celery -A cooplink worker --loglevel=info
```

---

## Production Deployment

### 1. Environment
Set `DJANGO_ENV=prod` and generate strong secrets:
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"   # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # MIRPAY_CALLBACK_SECRET (optional)
```

Set `ALLOWED_HOSTS`, `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` to real domains, and flip
`SECURE_SSL_REDIRECT=True` once the app terminates TLS (leave `False` behind a proxy that
already redirects — see `cooplink/settings/prod.py`).

### 2. Run with Docker / Procfile processes
* **web**: `gunicorn cooplink.wsgi --log-file -`
* **worker**: `celery -A cooplink worker --loglevel=info`
* **beat**: `celery -A cooplink beat --loglevel=info`
* **release**: `python manage.py migrate && python manage.py set_telegram_webhook`

The CI workflow lints (`ruff`) and runs the full test suite on every push to `master`;
the CD workflow builds and publishes a Docker image to GHCR on every push to `master`.

### 3. Payment providers
Gateway credentials can be set via env vars or configured at runtime in the Django admin
(**Payment Settings**), which takes precedence without a restart:
* **inPAY** — merchant id + token from inpay.uz. Server IP must be whitelisted (Strict mode).
* **MirPay** — kassa id + API key from mirpay.uz, plus an optional callback secret used to
  verify `X-MirPay-Signature` HMAC-SHA256 webhook signatures.
* Webhooks: `/api/payments/inpay/webhook/`, `/api/payments/mirpay/webhook/`.
* Browser return URLs: `/api/payments/{mirpay|inpay}/{success|cancel}/` forward the buyer
  to `<FRONTEND_URL>/payment/success|cancel`.

### 4. Payouts & fees
The seller withdrawal fee percentage is admin-configurable (**Withdrawal fee** singleton);
each payout request snapshots the fee so history stays accurate.

---

## Key Endpoints
* **Health Check**: `GET /api/health/` (Anonymous access enabled)
* **Admin Panel**: `GET /admin/` (Styled with `django-unfold`)
* **JWT Obtain Token**: `POST /api/token/`
* **JWT Refresh Token**: `POST /api/token/refresh/`

### Auth
| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| GET | `/api/auth/github/login/` | — | Returns GitHub authorization URL (scope: `read:user user:email`) |
| GET | `/api/auth/github/callback/` | — | Single callback for both flows; uses `state` param to distinguish login vs connect-repos |
| GET | `/api/auth/me/` | JWT | Returns current user's profile |
| PATCH | `/api/auth/me/` | JWT | Update `bio` and/or `avatar_url` |
| GET | `/api/auth/github/connect-repos/` | JWT | Returns authorization URL for `public_repo` scope (seller flow)

### Listings
| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| GET | `/api/listings/my-repos/` | JWT (seller) | List seller's GitHub repos |
| GET | `/api/listings/projects/` | JWT (seller) | List own projects |
| POST | `/api/listings/projects/` | JWT (seller) | Create new project (status: draft) |
| GET | `/api/listings/projects/{id}/` | JWT (seller) | Retrieve a project |
| PATCH | `/api/listings/projects/{id}/` | JWT (seller) | Update draft/rejected project |
| DELETE | `/api/listings/projects/{id}/` | JWT (seller) | Delete draft/rejected project |
| POST | `/api/listings/projects/{id}/submit/` | JWT (seller) | Move draft → pending_review |
| GET | `/api/listings/` | — | Public catalog (published only, paginated, filterable, searchable) |
| GET | `/api/listings/{slug}/` | — | Public project detail (increments `view_count`) |
| POST/PATCH/DELETE | `/api/listings/{slug}/rate/` | JWT (phone-verified) | Upsert/delete the user's rating & review |

### Payments
| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| GET | `/api/payments/providers/` | — | Enabled payment providers (for the checkout UI) |
| POST | `/api/payments/verify/` | JWT | Provider-agnostic verification (auto-detects inPAY/MirPay) |
| POST | `/api/payments/mirpay/verify/` | JWT | Verify a MirPay payment by `payid` |
| POST | `/api/payments/inpay/verify/` | JWT | Verify an inPAY payment by `order_id` |
| POST | `/api/payments/mirpay/webhook/` | — | Unified MirPay webhook (HMAC-verified when secret configured) |
| POST | `/api/payments/inpay/webhook/` | — | inPAY webhook (status re-verified before confirming orders and Crack It entries) |

### Crack It leaderboard
| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| GET | `/api/leaderboard/` | — | Ranked paid entries, totals, settings |
| POST | `/api/leaderboard/entries/` | — | Create a pending entry (domain, brand, bid ≥ min) |
| POST | `/api/leaderboard/entries/{id}/pay/` | — | Start inPAY checkout for the entry |
| POST | `/api/leaderboard/entries/{id}/click/` | — | Public outbound click counter |
| POST | `/api/leaderboard/verify/` | — | Post-checkout verification (re-checks status with inPAY) |

### Payouts
| Method | URL | Auth | Description |
|--------|-----|------|-------------|
| POST | `/api/payouts/request/` | JWT (seller) | Request a withdrawal (card encrypted at rest; fee + net snapshotted) |
| GET | `/api/payouts/mine/` | JWT (seller) | Own payouts, balance summary and current fee % |

#### Public catalog query params (`GET /api/listings/`)

| Param | Description |
|-------|-------------|
| `category` | Category slug (e.g. `web-apps`) |
| `tags` | Comma-separated tags (all must match) |
| `tech_stack` | Comma-separated tech values (all must match) |
| `min_price` / `max_price` | Price range (UZS) |
| `q` | Search title/description (Postgres full-text; `icontains` fallback on SQLite) |
| `ordering` | `created_at`, `-created_at`, `price`, `-price`, `view_count`, `-view_count` |
| `page` / `page_size` | Pagination (default page size: 20) |

---

## GitHub OAuth App Setup

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Fill in:
   - **Application name**: `Cooplink (local dev)`
   - **Homepage URL**: `http://localhost:8000`
   - **Authorization callback URL**: `http://localhost:8000/api/auth/github/callback/`
3. Click **Register application** and copy the **Client ID** and generate a **Client Secret**.
4. Paste them into your `.env`:
   ```
   GITHUB_CLIENT_ID=your_id_here
   GITHUB_CLIENT_SECRET=your_secret_here
   ```

   **Note**: Both login and connect-repos flows use the same callback URL. The flow type is encoded in a signed `state` parameter that GitHub round-trips back unchanged — no session cookie is required on callback.

   The token exchange sends `redirect_uri` and uses form-encoded POST data as GitHub requires.

### Fernet Key
Generate a fresh key and add it to `.env`:
```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → paste output as FERNET_KEY=...
```
