# inPAY API

> inPAY is a payment gateway for merchants in Uzbekistan. This REST API lets a
> merchant create payment invoices, check transaction status, fetch fiscal
> receipts (soliq.uz), and receive webhook notifications. Format: JSON.
> Authentication: Bearer token (valid 24h). All amounts are in UZS (so'm).

- Base URL: `https://inpay.uz/api/v1/`
- Human docs: https://inpay.uz/api/
- Auth: `Authorization: Bearer <token>` header on protected endpoints
- Content type: `application/json`
- Trailing slash on endpoints is REQUIRED (e.g. `/create/`, not `/create`)
- Support: Telegram @merchants_uz, @inPAYuz

## Quick start

1. Register on inPAY, create a cashbox (kassa), and get `merchant_id` + `merchant_token`.
2. `GET /authorization/` with those → receive a 24h `bearer_token`. Cache it.
3. `POST /create/` with the bearer token → receive `order_id` and `pay_url`.
4. Redirect the customer to `pay_url` to pay.
5. Receive a POST webhook at your `callback_url` when the payment status changes; respond HTTP 200.
6. Optionally `GET /transactions/?order_id=...` to poll status.

## Authentication — get a Bearer token

`GET /api/v1/authorization/`

Query parameters:
- `merchant_id` (integer, required) — merchant identifier
- `merchant_token` (string, required) — merchant secret token (32 chars)

Example:
```
curl -X GET "https://inpay.uz/api/v1/authorization/?merchant_id=1353&merchant_token=6a7bf375b302cfcda6692e6f60402cb3" \
  -H "Accept: application/json"
```

Success (200):
```json
{ "success": true, "bearer_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." }
```

The token is valid for 24 hours. Cache it server-side; do not request a new token on every call.

## Create a payment

`POST /api/v1/create/`

Headers:
```
Content-Type: application/json
Authorization: Bearer <your_bearer_token>
```

Body (JSON):
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| merchant_id | string | yes | Merchant ID |
| token | string | yes | Merchant token |
| amount | number | yes | Payment amount, minimum 1000 UZS |
| description | string | no | Human description of the payment |
| payment_method | string | no | One of: `click`, `payme`, `cardsystem` (inPAY). If omitted, the customer chooses on the checkout page. |
| callback_url | string | no | Webhook URL. Its domain must match the merchant's own website/webhook/callback domain, or be whitelisted. If omitted, the cashbox default is used. |
| phone | string | no | Customer phone, format `998901234567` |
| client_ip | string | no | Real payer (customer) IP address. Recommended for server-side integrations (e.g. Telegram bots/Mini Apps) where all requests originate from one server IP — lets inPAY record the actual payer's IP for anti-fraud/AML monitoring. Does NOT affect the connecting-IP whitelist logic; it is stored only in the audit trail. |

Example:
```
curl -X POST "https://inpay.uz/api/v1/create/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "merchant_id":    "1353",
    "token":          "6a7bf375b302cfcda6692e6f60402cb3",
    "amount":         15000,
    "description":    "Order #12345",
    "payment_method": "click",
    "phone":          "998901234567",
    "client_ip":      "203.0.113.10",
    "callback_url":   "https://merchant.uz/payment/callback"
  }'
```

Success (200):
```json
{
  "success": true,
  "order_id": "1ff2f5a6d66f6e9c",
  "pay_url": "https://inpay.uz/checkout/1ff2f5a6d66f6e9c",
  "phone": "998901234567",
  "message": "invoice yaratildi",
  "security": { "ip_mode": "optional", "ip_check": "IP verified (optional)" }
}
```

Redirect the customer to `pay_url`.

## Transaction status

`GET /api/v1/transactions/?order_id=...`

Query parameters:
- `order_id` (string, required) — the order_id returned by create

Example:
```
curl -X GET "https://inpay.uz/api/v1/transactions/?order_id=1ff2f5a6d66f6e9c" \
  -H "Accept: application/json"
```

Example response (200):
```json
{
  "success": true,
  "order_id": "1ff2f5a6d66f6e9c",
  "status": "success",
  "amount": 15000,
  "payment_method": "click",
  "created_at": "2025-12-10 05:14:52",
  "paid_at": "2025-12-10 05:15:23"
}
```

Status values:
- `pending` — awaiting payment
- `success` — paid successfully
- `failed` — payment failed
- `cancelled` — payment cancelled

## Fiscal receipt (soliq.uz)

`GET /api/v1/fiscal/?order_id=...&merchant_id=...`

Requires `Authorization: Bearer <token>`. Returns the fiscal receipt only if the order belongs to that merchant.

Query parameters:
- `order_id` (string, required) — payment order_id (Payme/Click)
- `merchant_id` (string, required) — merchant ID (token owner)

Example:
```
curl -X GET "https://inpay.uz/api/v1/fiscal/?order_id=1ff2f5a6d66f6e9c&merchant_id=1353" \
  -H "Authorization: Bearer <token>"
```

Example response (200):
```json
{
  "success": true,
  "data": {
    "order_id": "1ff2f5a6d66f6e9c",
    "status": "success",
    "fiscalized": true,
    "fiscal_url": "https://ofd.soliq.uz/epi?t=EP...&r=...&s=...",
    "fiscal_receipt_id": "52308469",
    "payment_method": "payme"
  }
}
```

## Webhook (payment notification)

When a payment status changes, inPAY sends a POST request to your `callback_url`.
Your handler MUST return HTTP 200; otherwise inPAY retries.

POST body (JSON):
| Field | Type | Description |
|-------|------|-------------|
| amount | string | Payment amount, e.g. "15000.00" |
| status | string | `success` or `failed` |
| order_id | string | Order identifier |
| transaction_id | integer | inPAY internal transaction ID |
| created_at | string | Timestamp (ISO-like format) |

Example payload:
```json
{
  "amount": "15000.00",
  "status": "success",
  "order_id": "1ff2f5a6d66f6e9c",
  "transaction_id": 149,
  "created_at": "2025-12-10 05:14:52"
}
```

Handler example (PHP):
```php
<?php
$data = json_decode(file_get_contents('php://input'), true);
if (!$data) { http_response_code(400); exit('Invalid JSON'); }
if (($data['status'] ?? '') === 'success') {
  $pdo->prepare('UPDATE orders SET status=?, paid_at=NOW() WHERE order_id=?')
      ->execute(['paid', $data['order_id']]);
}
http_response_code(200);
echo 'OK';
```

Rules:
- Send your webhook URL in `callback_url`; otherwise the cashbox default URL is used.
- The handler must accept JSON and return HTTP 200.
- The webhook/callback URL should use HTTPS.

## Error codes

Every error response has `success: false`, a human `message`, and a machine `error_code`.

| error_code | HTTP | Meaning |
|------------|------|---------|
| MISSING_AUTH_TOKEN | 401 | Authorization token missing |
| INVALID_TOKEN | 401 | Bearer token invalid or expired |
| MISSING_MERCHANT_ID | 400 | merchant_id not provided |
| MERCHANT_NOT_FOUND | 404 | Merchant not found |
| IP_NOT_WHITELISTED_STRICT | 403 | Caller IP not in whitelist (strict mode) |
| RATE_LIMIT_EXCEEDED | 429 | Too many requests (limit 100/hour per IP) |
| CALLBACK_NOT_WHITELISTED | 403 | callback_url domain not whitelisted |
| MERCHANT_WEBSITE_NOT_WHITELISTED | 403 | Merchant website not active in whitelist |
| AMOUNT_TOO_LOW | 400 | Amount below minimum (1000 UZS) |
| AMOUNT_TOO_HIGH | 400 | Amount exceeds the merchant maximum |
| TRANSACTION_SAVE_FAILED | 500 | Transaction could not be saved (server error) |

Example error:
```json
{ "success": false, "message": "Minimal to'lov summasi 1000 so'm", "error_code": "AMOUNT_TOO_LOW" }
```

## Security

- Bearer token valid 24h; store server-side, never expose it; refresh when expired.
- IP whitelist modes (per cashbox): `strict` (only whitelisted IPs allowed),
  `optional` (checked only if a whitelist exists), `disabled` (no IP check, test mode).
- Rate limit: 100 requests per hour per IP.
- Callback URL and merchant website must be whitelisted (auto-trusted if they match the merchant's own domains).
- Use HTTPS for all callback/webhook URLs.

## Best practices

- Cache the bearer token for 24h instead of requesting one per call.
- Prefer webhooks over polling for status; poll `/transactions/` only as a fallback.
- Always check `success`, then branch on `error_code`. Log all requests/responses.
- Persist `order_id` and transaction data in your own database.
- For server-side integrations (bots, Mini Apps), pass the real payer IP in `client_ip`.