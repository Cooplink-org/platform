import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

MIRPAY_TOKEN_CACHE_KEY = "mirpay_access_token"
MIRPAY_BASE = "https://mirpay.uz/api"


class MirPayError(Exception):
    pass


class MirPayClient:
    """Thin wrapper around the MirPay.uz payment gateway API."""

    def __init__(self):
        self.kassaid = settings.MIRPAY_KASSA_ID
        self.api_key = settings.MIRPAY_API_KEY

    # ── token management ─────────────────────────────────────────────────────

    def get_token(self) -> str:
        cached = cache.get(MIRPAY_TOKEN_CACHE_KEY)
        if cached:
            return cached

        url = f"{MIRPAY_BASE}/connect?kassaid={self.kassaid}&api_key={self.api_key}"
        resp = requests.post(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise MirPayError(f"MirPay token response missing token field: {data}")

        cache.set(MIRPAY_TOKEN_CACHE_KEY, token, timeout=3500)
        return token

    def _bearer_headers(self):
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _request(self, method, path, *, retried=False, **kwargs):
        url = f"{MIRPAY_BASE}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self._bearer_headers())
        resp = requests.request(method, url, headers=headers, timeout=20, **kwargs)

        if resp.status_code == 401 and not retried:
            cache.delete(MIRPAY_TOKEN_CACHE_KEY)
            return self._request(method, path, retried=True, **kwargs)

        resp.raise_for_status()
        return resp

    # ── payment lifecycle ────────────────────────────────────────────────────

    def create_payment(self, order):
        """
        POST /api/create-pay
        Creates a MirPay invoice for the given Order.

        TODO: Lock down the response field names after one real test call
        against MirPay's sandbox — the exact shape of the payment id/link
        is not yet confirmed.
        """
        import json

        amount_uzs = int(order.price_at_purchase)
        reference = f"Buyurtma ID: {order.id}"
        resp = self._request(
            "POST",
            f"/create-pay?summa={amount_uzs}&info_pay={reference}",
        )
        raw = resp.json()
        log.info("MirPay create_payment raw response: %s", json.dumps(raw, ensure_ascii=False))

        payid = raw.get("pay_id") or raw.get("payid") or raw.get("payment_id") or raw.get("id")
        payment_url = raw.get("url") or raw.get("payment_url") or raw.get("link") or raw.get("redirect_url")

        if not payid:
            raise MirPayError(
                f"Could not extract payment id from MirPay response. "
                f"Raw body: {json.dumps(raw, ensure_ascii=False)}"
            )

        return payid, payment_url, raw

    def check_status(self, payid):
        """
        POST /api/pay/invoice/  (form-encoded)
        Returns the authoritative payment status from MirPay.
        """
        import json

        resp = self._request(
            "POST",
            "/pay/invoice/",
            data={"payid": payid},
        )
        raw = resp.json()
        log.info("MirPay check_status raw response: %s", json.dumps(raw, ensure_ascii=False))
        return raw

    def get_balance(self):
        """GET /api/balans — returns current kassa balance."""
        resp = self._request("GET", "/balans")
        return resp.json()
