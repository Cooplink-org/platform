import contextlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

MIRPAY_TOKEN_CACHE_KEY = "mirpay_access_token"
MIRPAY_BASE = "https://mirpay.uz/api"


class MirPayError(Exception):
    pass


# Status detection across MirPay response variants. The docs are ambiguous about
# the exact `status` value for a paid invoice, so accept a broad set and check
# several plausible keys rather than assuming a single shape.
SUCCESS_STATUS_VALUES = {
    "success",
    "paid",
    "confirmed",
    "ok",
    "completed",
    "complete",
    "1",
    "true",
    "yes",
}
FAILED_STATUS_VALUES = {
    "failed",
    "fail",
    "error",
    "canceled",
    "cancelled",
    "rejected",
    "0",
    "false",
}


def is_success_status(raw) -> bool:
    """Best-effort detection of a successful payment from a MirPay response."""
    if not isinstance(raw, dict):
        return False
    value = (
        raw.get("status")
        or raw.get("state")
        or raw.get("status_code")
        or raw.get("code")
        or raw.get("result")
    )
    if value is not None:
        return str(value).strip().lower() in SUCCESS_STATUS_VALUES
    # Some gateways return a boolean flag instead of a status string.
    return bool(raw.get("success") or raw.get("paid") or raw.get("confirmed"))


def is_failed_status(raw) -> bool:
    """Best-effort detection of a failed/cancelled payment from a MirPay response."""
    if not isinstance(raw, dict):
        return False
    value = (
        raw.get("status")
        or raw.get("state")
        or raw.get("status_code")
        or raw.get("code")
        or raw.get("result")
    )
    if value is not None:
        return str(value).strip().lower() in FAILED_STATUS_VALUES
    return raw.get("success") is False or raw.get("paid") is False


class MirPayClient:
    """Thin wrapper around the MirPay.uz payment gateway API."""

    def __init__(self):
        self.kassaid = settings.MIRPAY_KASSA_ID
        self.api_key = settings.MIRPAY_API_KEY

    # ── token management ─────────────────────────────────────────────────────

    def get_token(self) -> str:
        try:
            cached = cache.get(MIRPAY_TOKEN_CACHE_KEY)
        except Exception:
            cached = None
        if cached:
            return cached

        url = f"{MIRPAY_BASE}/connect?kassaid={self.kassaid}&api_key={self.api_key}"
        resp = requests.post(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise MirPayError(f"MirPay token response missing token field: {data}")

        with contextlib.suppress(Exception):
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
            with contextlib.suppress(Exception):
                cache.delete(MIRPAY_TOKEN_CACHE_KEY)
            return self._request(method, path, retried=True, **kwargs)

        resp.raise_for_status()
        return resp

    # ── payment lifecycle ────────────────────────────────────────────────────

    def create_payment(self, order):
        """
        POST /api/create-pay
        Creates a MirPay invoice for the given Order.
        Returns (payid, payment_url, raw_response).
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

        # Try every plausible field name (case-insensitive)
        normalized = {k.lower(): v for k, v in raw.items()} if isinstance(raw, dict) else {}

        payid = (
            normalized.get("pay_id")
            or normalized.get("payid")
            or normalized.get("payment_id")
            or normalized.get("id")
            or raw.get("PayId")
            or raw.get("PaymentId")
            or raw.get("PayID")
        )

        payment_url = (
            normalized.get("url")
            or normalized.get("payment_url")
            or normalized.get("link")
            or normalized.get("redirect_url")
            or normalized.get("checkout_url")
            or normalized.get("pay_url")
            or raw.get("Url")
            or raw.get("PaymentUrl")
            or raw.get("RedirectUrl")
        )

        if not payid:
            raise MirPayError(
                f"Could not extract payment id from MirPay response. "
                f"Raw body: {json.dumps(raw, ensure_ascii=False)}"
            )

        # If MirPay didn't return a redirect URL, construct one from the payid.
        # MirPay's hosted payment page is at https://mirpay.uz/pay/<payid>
        if not payment_url:
            payment_url = f"https://mirpay.uz/pay/{payid}"
            log.info("MirPay did not return a redirect URL — constructed: %s", payment_url)

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
        try:
            raw = resp.json()
        except ValueError:
            # Gateways occasionally return an HTML error page or empty body —
            # fail loudly with the raw text logged instead of a cryptic error.
            log.error(
                "MirPay check_status returned non-JSON for payid=%s: %s",
                payid,
                resp.text[:1000],
            )
            raise MirPayError(
                f"MirPay check_status returned non-JSON response: {resp.text[:500]!r}"
            ) from None
        log.info("MirPay check_status raw response: %s", json.dumps(raw, ensure_ascii=False))
        return raw

    def get_balance(self):
        """GET /api/balans — returns current kassa balance."""
        resp = self._request("GET", "/balans")
        return resp.json()
