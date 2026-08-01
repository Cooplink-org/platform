from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class IsOnboarded(BasePermission):
    """Grant access only if the user has completed onboarding (profile + terms)."""

    def has_permission(self, request, _view):
        user = request.user
        if user.is_anonymous:
            return False
        return user.is_onboarded


class ActiveUserJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication that rejects tokens for inactive/banned users
    on every request, not just at login time.

    Without this, a banned user's access token continues to work for up to
    ACCESS_TOKEN_LIFETIME (60 min) after the ban because SimpleJWT's default
    get_user() doesn't always enforce is_active.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not user.is_active:
            raise InvalidToken("User account is disabled.")
        return user
