import environ
from pathlib import Path
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "django-insecure-e^xshk3zj&vqh7=l0qv-2bfd$e=%ns^1ke58$4bod_9@(pj5_k"),
    ALLOWED_HOSTS=(list, ["*"]),
    CORS_ALLOWED_ORIGINS=(list, []),
    CORS_ALLOW_ALL_ORIGINS=(bool, True),  # default to True for easy development, overridden in prod if needed
    CELERY_BROKER_URL=(str, "redis://localhost:6379/0"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/0"),
    GITHUB_CLIENT_ID=(str, ""),
    GITHUB_CLIENT_SECRET=(str, ""),
    GITHUB_CALLBACK_URL=(str, "http://localhost:8000/api/auth/github/callback/"),
    FERNET_KEY=(str, ""),
    FRONTEND_URL=(str, "http://localhost:8000"),
    MIRPAY_KASSA_ID=(str, ""),
    MIRPAY_API_KEY=(str, ""),
    TELEGRAM_BOT_TOKEN=(str, ""),
    TELEGRAM_API_ID=(str, ""),
    TELEGRAM_API_HASH=(str, ""),
)

# Read .env file if it exists
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

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
    "corsheaders",

    # Local apps
    "accounts",
    "listings",
    "orders",
    "payments",
    "payouts",
    "dashboard",
    "notifications",
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
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle"
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/day",
        "user": "2000/day",
        "burst": "10/minute",
    }
}

# SimpleJWT JWT Auth Settings
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
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
CELERY_BEAT_SCHEDULE = {
    "check-unlocked-earnings-daily": {
        "task": "notifications.tasks.daily_check_unlocked_earnings",
        "schedule": 86400.0,  # 24 hours in seconds
    },
}

# django-unfold Admin Settings
UNFOLD = {
    "SITE_TITLE": "Cooplink Admin",
    "SITE_HEADER": "Cooplink Admin",
    "DASHBOARD_CALLBACK": "dashboard.admin_dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Marketplace",
                "separator": True,
                "items": [
                    {
                        "title": "Projects",
                        "icon": "inventory_2",  # Material icon name
                        "link": "/admin/listings/project/",
                    },
                    {
                        "title": "Orders",
                        "icon": "shopping_cart",
                        "link": "/admin/orders/order/",
                    },
                ],
            },
            {
                "title": "Financials",
                "separator": True,
                "items": [
                    {
                        "title": "Ledger",
                        "icon": "account_balance_wallet",
                        "link": "/admin/orders/transaction/",
                    },
                    {
                        "title": "Payout Requests",
                        "icon": "payments",
                        "link": "/admin/payouts/payoutrequest/",
                    },
                ],
            },
            {
                "title": "Users",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "people",
                        "link": "/admin/accounts/user/",
                    },
                ],
            },
        ],
    },
}

# Custom User Model
AUTH_USER_MODEL = "accounts.User"

# GitHub OAuth Credentials
GITHUB_CLIENT_ID = env("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = env("GITHUB_CLIENT_SECRET")
# Single callback URL — both login and connect-repos flows use this.
# The flow is identified by the `state` param GitHub round-trips unchanged.
GITHUB_CALLBACK_URL = env("GITHUB_CALLBACK_URL")

# Fernet key for encrypting GitHub access tokens at rest.
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY = env("FERNET_KEY")

# Frontend URL for OAuth redirects
FRONTEND_URL = env("FRONTEND_URL")

# MirPay.uz Payment Gateway Credentials
MIRPAY_KASSA_ID = env("MIRPAY_KASSA_ID")
MIRPAY_API_KEY = env("MIRPAY_API_KEY")

# Redis cache backend (shared Redis instance)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
        "KEY_PREFIX": "cooplink",
    }
}
