"""
Middleware to track user IP addresses.

Updates `last_login_ip` on authenticated requests, throttled to at most
once per 5 minutes per user to avoid unnecessary DB writes.
Sets `signup_ip` on the first request after account creation.
"""

import contextlib

from django.contrib.auth import get_user_model
from django.core.cache import cache


def _get_client_ip(request):
    """Extract client IP, respecting X-Forwarded-For for proxied requests."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class IPTrackingMiddleware:
    """
    Track user IP addresses on each request.

    - `last_login_ip` is updated at most once per 5 minutes per user.
    - `signup_ip` is set once when the field is empty.
    """

    THROTTLE_SECONDS = 300  # 5 minutes

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not hasattr(request, "user") or not request.user.is_authenticated:
            return response

        ip = _get_client_ip(request)
        if not ip:
            return response

        user = request.user
        throttle_key = f"ip_tracking:{user.pk}:{ip}"

        # Skip if we already recorded this IP for this user recently
        with contextlib.suppress(Exception):
            if cache.get(throttle_key):
                return response

        update_fields = {}

        if user.last_login_ip != ip:
            update_fields["last_login_ip"] = ip

        if not user.signup_ip:
            update_fields["signup_ip"] = ip

        if update_fields:
            user_model = get_user_model()
            user_model.objects.filter(pk=user.pk).update(**update_fields)
            # Mark this IP as recorded for the throttle window
            with contextlib.suppress(Exception):
                cache.set(throttle_key, True, self.THROTTLE_SECONDS)

        return response
