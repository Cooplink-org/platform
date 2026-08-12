from datetime import timedelta
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, ""),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:3000"]),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/0"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/0"),
    GITHUB_CLIENT_ID=(str, ""),
    GITHUB_CLIENT_SECRET=(str, ""),
    GITHUB_CALLBACK_URL=(str, "http://localhost:8000/api/auth/github/callback/"),
    FERNET_KEY=(str, ""),
    FRONTEND_URL=(str, "http://localhost:8000"),
    MIRPAY_KASSA_ID=(str, ""),
    MIRPAY_API_KEY=(str, ""),
    INPAY_MERCHANT_ID=(str, ""),
    INPAY_MERCHANT_TOKEN=(str, ""),
    TELEGRAM_BOT_TOKEN=(str, ""),
    TELEGRAM_API_ID=(str, ""),
    TELEGRAM_API_HASH=(str, ""),
    TELEGRAM_BOT_USERNAME=(str, "cooplink_bot"),
    TELEGRAM_WEBHOOK_SECRET=(str, ""),
    TELEGRAM_BOT_API_SECRET_TOKEN=(str, ""),
    TELEGRAM_WEBHOOK_BASE_URL=(str, ""),
)

# Read .env file if it exists
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Refuse to start with an empty SECRET_KEY — this would silently break
# JWT signing, session cookies, and password reset tokens.
if not SECRET_KEY:
    raise ValueError(
        "SECRET_KEY is not set. Set it via the SECRET_KEY environment variable "
        "or a .env file. Generate one with: "
        'python -c "from django.core.management.utils import '
        'get_random_secret_key; print(get_random_secret_key())"'
    )

# Warn (don't crash) if FERNET_KEY is missing — it's only needed when a seller
# connects GitHub repos or a payout request encrypts a card number.
FERNET_KEY = env("FERNET_KEY")
if not FERNET_KEY and not DEBUG:
    import warnings

    warnings.warn(
        "FERNET_KEY is not set. GitHub token encryption and card number "
        "encryption will fail at runtime. Generate one with: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
        stacklevel=1,
    )

# Application definition
INSTALLED_APPS = [
    # django-unfold must be before django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.postgres",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party packages
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    # Local apps
    "accounts",
    "listings",
    "orders",
    "payments",
    "payouts",
    "dashboard",
    "notifications",
    "moderation",
    "django_celery_beat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # CORS middleware as high as possible
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.ip_middleware.IPTrackingMiddleware",
    "accounts.middleware.OnboardingGateMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cooplink.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cooplink.wsgi.application"

# Database connection
# Default to PostgreSQL, fall back to SQLite for local fallback if no DATABASE_URL is found
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework Configuration
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("accounts.permissions.ActiveUserJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/minute",
        "user": "100/minute",
        "burst": "30/minute",
    },
}

# SimpleJWT JWT Auth Settings
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# CORS headers Configuration
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = env("CORS_ALLOW_ALL_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# Celery Configurations
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# Explicitly set to avoid Celery 5.1 pending deprecation warning
# In Celery 5.1 this defaults to False but will be True in 6.0
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = False
# Keep the broker connection alive — managed Redis (e.g. Upstash) kills idle
# sockets after a few minutes, which crashes the worker on reconnect.
CELERY_BROKER_HEARTBEAT = 30
CELERY_BROKER_CONNECTION_TIMEOUT = 30
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Upstash closes idle connections after ~60 s; use short socket timeout so
    # Kombu detects the drop quickly and reconnects rather than hanging.
    "socket_timeout": 5,
    "socket_connect_timeout": 5,
    "socket_keepalive": True,
    # visibility_timeout must be >= the longest expected task runtime (seconds).
    # 3600 = 1 hour; tasks that take longer should use task.update_state heartbeats.
    "visibility_timeout": 3600,
    # Back off gently on reconnect so we don't hammer Upstash.
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 5,
    "retry_policy": {
        "timeout": 30,
        "max_retries": 10,
    },
}
CELERY_BEAT_SCHEDULE = {
    "check-unlocked-earnings-daily": {
        "task": "notifications.tasks.daily_check_unlocked_earnings",
        "schedule": 86400.0,  # 24 hours in seconds
    },
    "cleanup-expired-telegram-tokens": {
        "task": "notifications.tasks.cleanup_expired_telegram_tokens",
        "schedule": 3600.0,  # every hour
    },
}

# django-unfold Admin Settings
UNFOLD = {
    "SITE_TITLE": "Cooplink Admin",
    "SITE_HEADER": "Cooplink Admin",
    "SITE_URL": "/admin/",
    "DASHBOARD_CALLBACK": "dashboard.admin_dashboard.dashboard_callback",
    "COLORS": {
        "primary": {
            50: "#f0fdf4",
            100: "#dcfce7",
            200: "#bbf7d0",
            300: "#86efac",
            400: "#4ade80",
            500: "#22c55e",
            600: "#16a34a",
            700: "#15803d",
            800: "#166534",
            900: "#14532d",
            950: "#052e16",
        },
    },
    "EXTENSIONS": {
        "unfold.extensions.history.HistoryExtension": {},
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Dashboard",
                "separator": False,
                "collapsible": False,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": "/admin/",
                    },
                ],
            },
            {
                "title": "Marketplace",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Projects",
                        "icon": "inventory_2",
                        "link": "/admin/listings/project/",
                    },
                    {
                        "title": "Categories",
                        "icon": "category",
                        "link": "/admin/listings/category/",
                    },
                    {
                        "title": "Snapshots",
                        "icon": "photo_library",
                        "link": "/admin/listings/projectsnapshot/",
                    },
                ],
            },
            {
                "title": "Orders & Payments",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Orders",
                        "icon": "shopping_cart",
                        "link": "/admin/orders/order/",
                    },
                    {
                        "title": "Ledger",
                        "icon": "account_balance_wallet",
                        "link": "/admin/orders/transaction/",
                    },
                    {
                        "title": "Payment Settings",
                        "icon": "settings",
                        "link": "/admin/payments/paymentproviderconfig/",
                    },
                    {
                        "title": "Webhook Logs",
                        "icon": "webhook",
                        "link": "/admin/payments/webhooklog/",
                    },
                ],
            },
            {
                "title": "Payouts",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Payout Requests",
                        "icon": "payments",
                        "link": "/admin/payouts/payoutrequest/",
                    },
                ],
            },
            {
                "title": "Users & Accounts",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "people",
                        "link": "/admin/accounts/user/",
                    },
                    {
                        "title": "Telegram Tokens",
                        "icon": "send",
                        "link": "/admin/notifications/telegramlinkingtoken/",
                    },
                    {
                        "title": "Phone Verifications",
                        "icon": "phone_android",
                        "link": "/admin/notifications/phoneverificationcode/",
                    },
                ],
            },
            {
                "title": "Moderation",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "AI Code Reviews",
                        "icon": "psychology",
                        "link": "/admin/moderation/aicodereview/",
                    },
                    {
                        "title": "Reports",
                        "icon": "flag",
                        "link": "/admin/moderation/report/",
                    },
                    {
                        "title": "Audit Log",
                        "icon": "history",
                        "link": "/admin/moderation/moderationlog/",
                    },
                ],
            },
        ],
    },
    "TABS": [
        {
            "title": "Platform",
            "items": [
                {
                    "title": "Projects",
                    "link": "/admin/listings/project/",
                    "permission": lambda request: request.user.is_staff,
                },
                {
                    "title": "Orders",
                    "link": "/admin/orders/order/",
                    "permission": lambda request: request.user.is_staff,
                },
                {
                    "title": "Users",
                    "link": "/admin/accounts/user/",
                    "permission": lambda request: request.user.is_staff,
                },
            ],
        },
    ],
}

# Custom User Model
AUTH_USER_MODEL = "accounts.User"

# GitHub OAuth Credentials
GITHUB_CLIENT_ID = env("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = env("GITHUB_CLIENT_SECRET")
# Single callback URL — both login and connect-repos flows use this.
# The flow is identified by the `state` param GitHub round-trips unchanged.
GITHUB_CALLBACK_URL = env("GITHUB_CALLBACK_URL")

# Frontend URL for OAuth redirects
FRONTEND_URL = env("FRONTEND_URL")

# Current Terms of Service / Privacy Policy version.
# Bump this string whenever the legal documents change.
# Users whose terms_accepted_version != CURRENT_TERMS_VERSION will be gated
# by the onboarding middleware until they accept the new version.
CURRENT_TERMS_VERSION = "2025-07-v1"

# MirPay.uz Payment Gateway Credentials
MIRPAY_KASSA_ID = env("MIRPAY_KASSA_ID")
MIRPAY_API_KEY = env("MIRPAY_API_KEY")

# inPAY (inpay.uz) Payment Gateway Credentials
# These are fallback defaults — the admin can override them via the
# PaymentProviderConfig model in the Django admin panel. The DB config
# takes precedence when the provider is enabled.
INPAY_MERCHANT_ID = env("INPAY_MERCHANT_ID")
INPAY_MERCHANT_TOKEN = env("INPAY_MERCHANT_TOKEN")

# Redis cache backend (shared Redis instance).
# OPTIONS: use short socket timeouts so a dropped Upstash idle connection
# raises immediately instead of hanging the request thread for 30+ seconds.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
        "KEY_PREFIX": "cooplink",
        "OPTIONS": {
            "socket_timeout": 5,
            "socket_connect_timeout": 5,
            "retry_on_timeout": False,
        },
    }
}

# Logging
# Default Django logging hides INFO messages from app loggers, which is why
# MirPay webhook/verification activity never showed up in the console. Enable
# console logging at INFO for the payment-critical loggers so every payment
# event (webhook raw body, check_status response, order confirmations) is
# visible during development and diagnosis.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        # Payment lifecycle — MUST be visible: webhook bodies, check_status,
        # order confirmation/failure decisions.
        "payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "orders": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "notifications.tasks": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Incoming HTTP requests (incl. MirPay webhooks) — one line per request.
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# Alibaba Model Studio Credentials & Configuration
ALIBABA_MODEL_STUDIO_API_KEY = env("ALIBABA_MODEL_STUDIO_API_KEY", default="")
ALIBABA_MODEL_STUDIO_ENDPOINT = env(
    "ALIBABA_MODEL_STUDIO_ENDPOINT",
    default="https://ws-yv2o93ke9xiugaok.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
)


# Ordered fallback queue for AI Code Reviewer
AI_REVIEW_MODELS = [
    "qwen3.6-plus",
    "qwen3.5-plus-2026-02-15",
    "qwen3.7-max-2026-06-08",
    "qwen3.7-max-preview",
    "qwen3.6-max-preview",
    "qwen3-32b",
    "qwen-plus-2025-07-28",
    "qwen3-max",
    "qwen-max",
    "qwen3.5-122b-a10b",
    "qwen3.5-397b-a17b",
    "glm-5.2",
    "glm-5.1",
    "kimi-k2.7-code",
    "qwen-mt-flash",
    "qwen3-vl-32b-thinking",
    "qwen3-vl-30b-a3b-thinking",
    "qwen3-vl-235b-a22b-thinking",
    "qwen3-235b-a22b-thinking-2507",
    "qwen-vl-ocr-2025-11-20",
    "qwen3.5-livetranslate-flash-realtime-2026-05-19",
    "qwen3.5-livetranslate-flash-realtime",
    "qwen3.5-flash",
    "qwen3.7-flash",
    "qwen-plus",
    "qwen-turbo",
]


