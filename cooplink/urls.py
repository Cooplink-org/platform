from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import health_check, serve_playground

urlpatterns = [
    path("", serve_playground, name="playground"),
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health_check"),
    path(
        "auth/callback/",
        RedirectView.as_view(
            url="/#auth/callback",
            query_string=True,
            permanent=False,
        ),
    ),
    path(
        "auth/github/callback/",
        RedirectView.as_view(
            pattern_name="github_callback",
            query_string=True,
            permanent=False,
        ),
    ),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/", include("accounts.urls")),
    path("api/listings/", include("listings.urls")),
    path("api/orders/", include("orders.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/leaderboard/", include("leaderboard.urls")),
    path("api/payouts/", include("payouts.urls")),
    path("api/dashboard/", include("dashboard.urls")),
    path("api/moderation/", include("moderation.urls")),
    path("api/telegram/", include("notifications.urls")),
]
