from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication

EXEMPT_PATHS = (
    "/api/auth/",
    "/api/token/",
    "/api/health/",
    "/api/telegram/",
    "/admin/",
)


class OnboardingGateMiddleware(MiddlewareMixin):
    """
    Block authenticated users with incomplete onboarding profiles from
    accessing any non-exempt API endpoint.

    DRF authentication runs in the view layer (after Django middleware), so
    we attempt JWT authentication here to catch API requests early.
    """

    def process_view(self, request, _view_func, _view_args, _view_kwargs):
        user = request.user
        # If Django's auth middleware didn't set a real user, try DRF JWT auth.
        if user.is_anonymous:
            try:
                result = JWTAuthentication().authenticate(request)
                if result is not None:
                    user, _ = result
            except Exception:
                pass

        # Allow anonymous users through (they may be hitting public endpoints).
        if user.is_anonymous:
            return None

        # Allow exempt paths (auth, token, health, admin).
        path = request.path_info
        if any(path.startswith(exempt) for exempt in EXEMPT_PATHS):
            return None

        # Block users who haven't completed onboarding.
        if not user.is_onboarded:
            return JsonResponse(
                {
                    "detail": "You must complete onboarding before accessing this resource.",
                    "onboarding_required": True,
                },
                status=403,
            )

        return None
