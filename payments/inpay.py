import contextlib
import json
import logging

import requests
from django.core.cache import cache

from .models import PaymentProviderConfig

log = logging.getLogger(__name__)

INPAY_TOKEN_CACHE_KEY = "inpay_access_token"
INPAY_BASE = "https://inpay.uz/api/v1"


class InPayError(Exception):
    pass


SUCCESS_STATUS = "success"
FAILED_STATUSES = {"failed", "cancelled"}


class InPayClient:
    """Thin wrapper around the inPAY (inpay.uz) payment gateway API.

    Reads credentials from the database (PaymentProviderConfig) so the admin
    panel can configure/enable/disable the provider without touching .env.
    """

    def __init__(self, config=None):
        if config is None:
            config = PaymentProviderConfig.objects.filter(
                provider=PaymentProviderConfig.Provider.INPAY, enabled=True
            ).first()
        if not config:
            raise InPayError("inPAY is not configured or disabled")
        if not config.merchant_id or not config.merchant_token:
            raise InPayError("inPAY merchant_id or merchant_token is not set")
        self.config = config
        self.merchant_id = config.merchant_id
        self.merchant_token = config.merchant_token

    # ── token management ─────────────────────────────────────────────────────

    def get_token(self) -> str:
        """GET /authorization/ — obtain a 24-hour Bearer token (cached in Redis)."""
        try:
            cached = cache.get(INPAY_TOKEN_CACHE_KEY)
        except Exception:
            cached = None
        if cached:
            return cached

        resp = requests.get(
            f"{INPAY_BASE}/authorization/",
            params={
                "merchant_id": self.merchant_id,
                "merchant_token": self.merchant_token,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("bearer_token")
        if not token:
            raise InPayError(f"inPAY token response missing bearer_token: {data}")

        # Tokens are valid 24 hours; cache for 23h to be safe.
        with contextlib.suppress(Exception):
            cache.set(INPAY_TOKEN_CACHE_KEY, token, timeout=82800)
        return token

    def _bearer_headers(self):
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _clear_cached_token():
        with contextlib.suppress(Exception):
            cache.delete(INPAY_TOKEN_CACHE_KEY)

    @staticmethod
    def _is_token_error(raw) -> bool:
        """inPAY reports bad/expired bearer tokens as HTTP 200 + success=false
        (error_code INVALID_TOKEN) instead of a 401/403 status."""
        if not isinstance(raw, dict):
            return False
        code = str(raw.get("error_code") or "").upper()
        message = str(raw.get("message") or "").lower()
        if raw.get("success") is not False:
            return False
        return code == "INVALID_TOKEN" or "bearer token" in message

    def _request(self, method, path, *, retried=False, **kwargs):
        url = f"{INPAY_BASE}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self._bearer_headers())
        resp = requests.request(method, url, headers=headers, timeout=20, **kwargs)

        if resp.status_code in (401, 403) and not retried:
            self._clear_cached_token()
            return self._request(method, path, retried=True, **kwargs)

        if not resp.ok:
            log.error(
                "inPAY %s %s failed: HTTP %s body=%s",
                method,
                url,
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        return resp

    def _send(self, method, path, *, retried=False, **kwargs):
        """_request + JSON parse, re-authenticating once when inPAY flags the
        bearer token via its HTTP-200 error envelope."""
        resp = self._request(method, path, retried=retried, **kwargs)
        try:
            raw = resp.json()
        except ValueError:
            log.error("inPAY %s %s returned non-JSON: %s", method, path, resp.text[:500])
            raise InPayError(
                f"inPAY {method} {path} returned non-JSON response: {resp.text[:500]!r}"
            ) from None
        if self._is_token_error(raw):
            if retried:
                raise InPayError(f"inPAY {method} {path} rejected token after refresh: {raw}")
            self._clear_cached_token()
            return self._send(method, path, retried=True, **kwargs)
        return raw

    # ── payment lifecycle ────────────────────────────────────────────────────

    def create_payment(
        self, order, client_ip: str = "", *, amount=None, description=None, return_url=None
    ):
        """POST /create/ — create a payment transaction.

        Returns (order_id, pay_url, raw_response).
        The inPAY order_id is stored on Order.payment_ref for later matching.
        ``client_ip`` is the real payer IP; it is recorded in inPAY's audit
        trail only and does not affect the connecting-IP whitelist.

        ``amount``/``description``/``return_url`` override the Order-derived
        defaults, for non-Order payments (e.g. leaderboard bids).
        """
        amount_uzs = int(amount if amount is not None else order.price_at_purchase)
        body = {
            "merchant_id": self.merchant_id,
            "token": self.merchant_token,
            "amount": amount_uzs,
            "description": description or f"Buyurtma ID: {order.id}",
        }
        if client_ip:
            body["client_ip"] = client_ip
        if self.config.callback_url:
            body["callback_url"] = self.config.callback_url
        effective_return_url = return_url or self.config.return_url
        if effective_return_url:
            body["return_url"] = effective_return_url

        raw = self._send("POST", "/create/", json=body)
        log.info("inPAY create_payment raw response: %s", json.dumps(raw, ensure_ascii=False))

        if not raw.get("success"):
            raise InPayError(f"inPAY create_payment failed: {raw}")

        order_id = raw.get("order_id")
        pay_url = raw.get("pay_url")

        if not order_id:
            raise InPayError(
                f"Could not extract order_id from inPAY response. "
                f"Raw body: {json.dumps(raw, ensure_ascii=False)}"
            )

        if not pay_url:
            raise InPayError(f"inPAY create_payment returned no pay_url: {raw}")

        return order_id, pay_url, raw

    def check_status(self, order_id):
        """GET /transactions/?order_id=... — check payment status.

        This endpoint does not require auth per the inPAY docs, but we send
        the Bearer token anyway for consistency and in case inPAY tightens this.
        """
        raw = self._send(
            "GET",
            "/transactions/",
            params={"order_id": order_id},
        )
        log.info("inPAY check_status raw response: %s", json.dumps(raw, ensure_ascii=False))
        return raw

    def get_fiscal(self, order_id):
        """GET /fiscal/?order_id=...&merchant_id=... — get fiscal receipt URL."""
        resp = self._request(
            "GET",
            "/fiscal/",
            params={"order_id": order_id, "merchant_id": self.merchant_id},
        )
        return resp.json()
