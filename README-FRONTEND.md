# Cooplink — Frontend Integration Guide

This document provides a comprehensive reference for integrating a frontend with the Cooplink API.

## 1. Environment & API Base
- **Local Dev API**: `http://localhost:8000`
- **Auth Scheme**: Bearer Token (JWT). Send `Authorization: Bearer <token>` in headers.

## 2. Authentication (GitHub OAuth)
Cooplink uses GitHub OAuth for both login and seller activation.

### Flow Architecture
1. **Initiate**: App calls `GET /api/auth/github/login/` -> receives `{ authorization_url }`.
2. **Redirect**: App redirects user to GitHub.
3. **Handle Callback**: User is redirected back to `FRONTEND_URL` with tokens in the hash fragment:
   `https://frontend.com/#auth/callback&access=<JWT>&refresh=<JWT>`
4. **Persist**: The frontend should parse these tokens and store them (e.g., in `localStorage`).

> [!NOTE]
> Sellers must perform an incremental "Connect Repos" flow via `GET /api/auth/github/connect-repos/` to enable listing creation. This updates the user's `is_seller` status.

---

## 3. API Endpoints

### 📦 Marketplace (Public)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/listings/` | Paginated catalog of published projects. |
| `GET` | `/api/listings/<slug>/` | Full details for a project. |
| `GET` | `/api/listings/categories/` | List of categories for filtering. |

**Query Parameters for `/api/listings/`**:
- `q`: Search title or description (keyword).
- `category`: Category slug (e.g., `web-apps`).
- `tags`: Comma-separated tags (filters project matching ALL tags).
- `tech_stack`: Comma-separated tech (e.g., `python,postgres`).
- `min_price` / `max_price`: Price range in UZS.
- `ordering`: `-created_at` (default), `price`, `-price`, `view_count`, `-view_count`.

### 🛠️ Seller Tools (Authorized)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/listings/my-repos/` | List user's GitHub repositories. |
| `POST` | `/api/listings/projects/` | Create a new project draft. |
| `PATCH` | `/api/listings/projects/<id>/` | Edit draft (blocked for published). |
| `POST` | `/api/listings/projects/<id>/submit/` | Submit for approval (`{"accept_terms": true}`). |

### 💰 Orders & Payouts (Authorized)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/orders/` | Initiate purchase. Payload: `{"project_id": <id>}`. Returns `redirect_url` to MirPay. |
| `GET` | `/api/orders/<id>/download/` | Fetch source ZIP archive for a paid project. |
| `GET` | `/api/payouts/mine/` | Returns balance info and payout history. |
| `POST` | `/api/payouts/request/` | Submit withdrawal request: `{"amount": <UZS>, "card_number": <str>}`. |

**Balance Data in `/api/payouts/mine/`**:
- `available_balance`: Sum available for withdrawal now.
- `pending_balance`: List of `{ amount, unlocks_at }` representing the 7-day hold for recent sales.

### 📊 Dashboard (Authorized)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/dashboard/summary/` | Totals: revenue, sales, downloads, and next unlock date. |
| `GET` | `/api/dashboard/sales/` | List of items sold to others. |
| `GET` | `/api/dashboard/listings/` | List of your listings with per-listing stats. |
| `GET` | `/api/dashboard/earnings-timeseries/` | Data for charts. Query param: `range=30d` (default). |

---

## 4. User Profile & Notifications
- `GET /api/auth/me/`: Current user data.
- `PATCH /api/auth/me/`: Update `bio`, `avatar_url`, or `telegram_chat_id`.

---

## 5. Development Resources
- **API Tester**: Open the root `/` of the backend in your browser to access the **Dev Playground**. It includes a built-in "API Tester" where you can verify payload shapes and status codes for every endpoint mentioned above.
- **Error Format**: Most errors return a standard JSON object: `{"detail": "Error message content"}`.
