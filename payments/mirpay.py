import contextlib
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .models import PaymentProviderConfig

log = logging.getLogger(__name__)

MIRPAY_TOKEN_CACHE_KEY = "mirpay_access_token"
MIRPAY_BASE = "https://mirpay.uz/api/v2"


class MirPayError(Exception):
    pass


# MirPay v2 uses Uzbek status strings
SUCCESS_STATUS_VALUES = {
    "muvaffaqiyatli!",
    "muvaffaqiyatli",
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
    "bekor qilingan!",
    "bekor qilingan",
    "bekor qilindi!",
    "bekor qilindi",
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
    """Thin wrapper around the MirPay.uz v2 payment gateway API.

    Reads credentials from the database (PaymentProviderConfig) when a MirPay
    config row exists and is enabled, falling back to env settings for
    backward compatibility.
    """

    def __init__(self):
        self._kassaid = None
        self._api_key = None

    def _ensure_credentials(self):
        if self._kassaid is not None:
            return
        try:
            config = PaymentProviderConfig.objects.filter(
                provider=PaymentProviderConfig.Provider.MIRPAY, enabled=True
            ).first()
            if config and config.merchant_id and config.merchant_token:
                self._kassaid = config.merchant_id
                self._api_key = config.merchant_token
                return
        except Exception:
            pass
        self._kassaid = settings.MIRPAY_KASSA_ID
        self._api_key = settings.MIRPAY_API_KEY

    @property
    def kassaid(self):
        self._ensure_credentials()
        return self._kassaid

    @property
    def api_key(self):
        self._ensure_credentials()
        return self._api_key

    # ── token management ─────────────────────────────────────────────────────

    # ── token management ─────────────────────────────────────────────────────

    def get_token(self) -> str:
        try:
            cached = cache.get(MIRPAY_TOKEN_CACHE_KEY)
        except Exception:
            cached = None
        if cached:
            return cached

        try:
            kassa_id = int(self.kassaid)
        except (ValueError, TypeError):
            kassa_id = self.kassaid

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        }

        url_v2 = f"{MIRPAY_BASE}/kassa/token"
        resp = requests.post(
            url_v2,
            json={"kassaid": kassa_id, "api_key": self.api_key},
            headers=headers,
            timeout=15,
        )

        # Fallback to v1 endpoint (/api/connect) if v2 returns 404 or 403
        if resp.status_code in (403, 404):
            v1_url = "https://mirpay.uz/api/connect"
            v1_resp = requests.post(
                v1_url,
                json={"kassaid": kassa_id, "api_key": self.api_key},
                headers=headers,
                timeout=15,
            )
            if v1_resp.status_code == 200:
                resp = v1_resp

        if resp.status_code == 403:
            raise MirPayError(
                "MirPay API returned 403 Forbidden. "
                "Please check your Kassa ID & API key in MirPay dashboard, "
                "and ensure your server IP is added to the IP Whitelist under Kassa settings at https://mirpay.uz."
            )

        resp.raise_for_status()
        data = resp.json()

        # v2 wraps data in {"success": true, "data": {"access_token": "..."}}
        payload = data.get("data", data) if isinstance(data, dict) else {}
        token = (
            payload.get("access_token") or payload.get("token")
            if isinstance(payload, dict)
            else None
        )
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
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        resp = requests.request(method, url, headers=headers, timeout=20, **kwargs)

        if resp.status_code == 401 and not retried:
            with contextlib.suppress(Exception):
                cache.delete(MIRPAY_TOKEN_CACHE_KEY)
            return self._request(method, path, retried=True, **kwargs)

        if resp.status_code == 403:
            raise MirPayError(
                "MirPay API returned 403 Forbidden. "
                "Verify your server IP is whitelisted in MirPay dashboard (Kassalarim settings)."
            )

        resp.raise_for_status()
        return resp

    # ── payment lifecycle ────────────────────────────────────────────────────

    def create_payment(self, order):
        """
        POST /api/v2/pay
        Creates a MirPay invoice for the given Order.
        Returns (payid, payment_url, raw_response).
        """
        amount_uzs = int(order.price_at_purchase)
        if amount_uzs < 1000 or amount_uzs > 100000000:
            raise MirPayError(
                f"MirPay payment amount {amount_uzs} UZS is outside the allowed range "
                "(1,000 to 100,000,000 UZS)."
            )

        reference = f"Buyurtma ID: {order.id}"
        resp = self._request(
            "POST",
            "/pay",
            json={"summa": amount_uzs, "info_pay": reference},
        )
        raw = resp.json()
        log.info("MirPay create_payment raw response: %s", json.dumps(raw, ensure_ascii=False))

        # v2 response: {"success": true, "data": {"payid": ..., "payinfo": {...}}}
        payload = raw.get("data", raw) if isinstance(raw, dict) else {}

        payid = (
            payload.get("payid") or payload.get("PayId") or payload.get("id")
            if isinstance(payload, dict)
            else None
        )

        payinfo = (
            payload.get("payinfo", {})
            if isinstance(payload, dict) and isinstance(payload.get("payinfo"), dict)
            else {}
        )
        payment_url = (
            payinfo.get("redirect_url")
            or payinfo.get("redicet_url")
            or payinfo.get("url")
            or payinfo.get("payment_url")
            or (payload.get("redirect_url") if isinstance(payload, dict) else None)
            or (payload.get("redicet_url") if isinstance(payload, dict) else None)
        )

        if not payid:
            raise MirPayError(
                f"Could not extract payment id from MirPay response. "
                f"Raw body: {json.dumps(raw, ensure_ascii=False)}"
            )

        if not payment_url:
            payment_url = f"https://mirpay.uz/pay/{payid}"
            log.info("MirPay did not return a redirect URL — constructed: %s", payment_url)

        return payid, payment_url, raw

    def check_status(self, payid):
        """
        GET /api/v2/pay/{payid}
        Returns the authoritative payment status from MirPay.
        """
        resp = self._request("GET", f"/pay/{payid}")
        try:
            raw = resp.json()
        except ValueError:
            log.error(
                "MirPay check_status returned non-JSON for payid=%s: %s",
                payid,
                resp.text[:1000],
            )
            raise MirPayError(
                f"MirPay check_status returned non-JSON response: {resp.text[:500]!r}"
            ) from None
        log.info("MirPay check_status raw response: %s", json.dumps(raw, ensure_ascii=False))

        # v2 wraps in {"success": true, "data": {"payinfo": {...}}}
        payload = raw.get("data", raw) if isinstance(raw, dict) else {}
        if isinstance(payload, dict) and isinstance(payload.get("payinfo"), dict):
            payinfo = payload["payinfo"]
        else:
            payinfo = payload
        return payinfo

    def get_balance(self):
        """GET /api/v2/balance — returns current kassa balance."""
        resp = self._request("GET", "/balance")
        raw = resp.json()
        # v2 wraps in {"success": true, "data": {"kassa_id": ..., "balans": ...}}
        return raw.get("data", raw) if isinstance(raw, dict) else raw
