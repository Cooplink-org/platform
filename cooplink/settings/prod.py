# Production settings — overrides base.py
import os

DEBUG = False

# Security hardening for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Redirect HTTP → HTTPS. Enable once TLS terminates on the app itself; leave
# off when a reverse proxy / load balancer handles the redirect.
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "False").lower() in (
    "1",
    "true",
    "yes",
)

# Trust the X-Forwarded-Proto header from the proxy for secure-request detection.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Send cookies only over same-site requests (CSRF hardening).
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Behind a proxy, use X-Forwarded-For so rate limiting and IP logging see real clients.
USE_X_FORWARDED_HOST = os.environ.get("USE_X_FORWARDED_HOST", "False").lower() in (
    "1",
    "true",
    "yes",
)
