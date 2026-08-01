import os
import secrets
import urllib.parse

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import OnboardingSerializer, UserSerializer
from .utils import encrypt_token

User = get_user_model()

# ── constants ─────────────────────────────────────────────────────────────────

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"

# OAuth flow identifiers embedded in the signed `state` param.
_FLOW_LOGIN = "login"
_FLOW_CONNECT_REPOS = "connect_repos"
_STATE_SALT = "github-oauth-state"
_STATE_MAX_AGE = 600  # seconds


# ── helpers ───────────────────────────────────────────────────────────────────


def _build_signed_state(flow: str, user_pk: int | None = None) -> str:
    """
    Return a signed, opaque state token for GitHub OAuth.

    We sign the payload instead of relying on session cookies because the
    callback is a cross-site redirect from github.com — browsers often do not
    send SameSite session cookies on that hop, which broke CSRF validation.
    """
    payload = {"flow": flow, "nonce": secrets.token_urlsafe(16)}
    if user_pk is not None:
        payload["user_pk"] = user_pk
    return signing.dumps(payload, salt=_STATE_SALT)


def _load_signed_state(state: str) -> dict:
    """Verify signature and expiry; raise signing.BadSignature on failure."""
    return signing.loads(state, salt=_STATE_SALT, max_age=_STATE_MAX_AGE)


def _exchange_code_for_token(code: str, state: str = "") -> str | None:
    """
    Exchange a GitHub OAuth code for an access token. Returns None on failure.

    We do NOT send `redirect_uri` here because it was also omitted from the
    initial authorize request — GitHub docs require the two to match, and by
    omitting it both times GitHub uses the registered callback URL from the
    app settings, which avoids any mismatch issues.
    """
    data = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
    }
    if state:
        data["state"] = state

    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data=data,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("access_token")


def _fetch_github_profile(token: str) -> dict:
    """Fetch /user from the GitHub API."""
    resp = requests.get(
        f"{GITHUB_API_BASE}/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_github_primary_email(token: str) -> str | None:
    """Fetch the primary verified email from GitHub (handles private emails)."""
    resp = requests.get(
        f"{GITHUB_API_BASE}/user/emails",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10,
    )
    if not resp.ok:
        return None
    for entry in resp.json():
        if entry.get("primary") and entry.get("verified"):
            return entry["email"]
    return None


def _issue_jwt(user: User) -> dict:
    """Return access and refresh JWT strings for the given user."""
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _upsert_user(profile: dict, gh_token: str) -> User:
    """Create or update a User from a GitHub profile dict."""
    github_id = str(profile["id"])
    github_username = profile.get("login", "")
    avatar_url = profile.get("avatar_url", "")
    email = profile.get("email") or _fetch_github_primary_email(gh_token) or ""

    user = User.objects.filter(github_id=github_id).first()
    if user is None:
        user = User.objects.filter(email=email).first() if email else None

    if user is None:
        base_username = github_username or f"gh_{github_id}"
        username = base_username
        suffix = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{suffix}"
            suffix += 1
        user = User(username=username, email=email)
        user.set_unusable_password()

    user.github_id = github_id
    user.github_username = github_username
    user.avatar_url = avatar_url
    user.email = email
    user.save()
    return user


# ── initiate flows ────────────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([AllowAny])
def github_login(_request):
    """
    GET /api/auth/github/login/
    Returns the GitHub authorization URL for the login flow.
    Scope: read:user user:email only — repo access is a separate incremental step.

    The `state` parameter is a signed token verified on callback (no session cookie
    required — OAuth redirects from GitHub often drop SameSite session cookies).
    """
    state = _build_signed_state(_FLOW_LOGIN)

    params = urllib.parse.urlencode(
        {
            "client_id": settings.GITHUB_CLIENT_ID,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return Response({"authorization_url": f"{GITHUB_AUTHORIZE_URL}?{params}"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_connect_repos(request):
    """
    GET /api/auth/github/connect-repos/
    Returns the GitHub authorization URL for the seller repo-access flow.
    Scope: public_repo only (MVP — public repos).

    # TODO(later): upgrade to `repo` scope for private-repo support with explicit
    # seller consent and a privacy notice.

    The signed `state` embeds the user pk so the callback can associate the token
    with the right account without needing the user to be authenticated at callback
    time (GitHub doesn't send our JWT cookies).
    """
    state = _build_signed_state(_FLOW_CONNECT_REPOS, user_pk=request.user.pk)

    params = urllib.parse.urlencode(
        {
            "client_id": settings.GITHUB_CLIENT_ID,
            "scope": "public_repo",
            "state": state,
        }
    )
    return Response({"authorization_url": f"{GITHUB_AUTHORIZE_URL}?{params}"})


# ── unified callback ──────────────────────────────────────────────────────────


def _oauth_redirect_on_error(message: str) -> Response | None:
    """
    Redirect the browser to the frontend with an error parameter in the hash fragment.
    Used when the OAuth callback encounters a failure.
    """
    from_url = settings.FRONTEND_URL
    url = f"{from_url}/#auth/callback&error={urllib.parse.quote(message)}"
    return redirect(url)


@api_view(["GET"])
@permission_classes([AllowAny])
def github_callback(request):
    """
    GET /api/auth/github/callback/
    Single callback URL for both OAuth flows. GitHub OAuth apps only support one
    callback URL per app, so we distinguish flows via the signed `state` param:

      flow=login          → create/update user, redirect to frontend with JWTs
      flow=connect_repos  → store encrypted token, set is_seller=True, redirect

    Security: `state` is a signed payload verified with Django's signing module.
    On success the browser is redirected to FRONTEND_URL/auth/callback with
    access/refresh tokens in the URL fragment (never sent to the server).
    On error the browser is redirected with an ?error= query param.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")

    if not code or not state:
        return _oauth_redirect_on_error("Missing code or state parameter.")

    try:
        payload = _load_signed_state(state)
    except signing.BadSignature:
        return _oauth_redirect_on_error("Invalid state parameter.")

    flow = payload.get("flow", "")

    # --- Exchange code for token ---
    try:
        gh_token = _exchange_code_for_token(code, state)
    except requests.RequestException as exc:
        return _oauth_redirect_on_error(f"GitHub token exchange failed: {exc}")

    if not gh_token:
        return _oauth_redirect_on_error("GitHub did not return an access token.")

    # ── branch: login ──────────────────────────────────────────────────────────
    if flow == _FLOW_LOGIN:
        try:
            profile = _fetch_github_profile(gh_token)
        except requests.RequestException as exc:
            return _oauth_redirect_on_error(f"GitHub profile fetch failed: {exc}")

        # Token is NOT persisted — only used here to read profile/email.
        user = _upsert_user(profile, gh_token)
        tokens = _issue_jwt(user)
        url = (
            f"{settings.FRONTEND_URL}/#auth/callback"
            f"&access={tokens['access']}&refresh={tokens['refresh']}"
        )
        return redirect(url)

    # ── branch: connect_repos ──────────────────────────────────────────────────
    if flow == _FLOW_CONNECT_REPOS:
        user_pk = payload.get("user_pk")
        if user_pk is None:
            return _oauth_redirect_on_error("Malformed state for connect_repos flow.")
        try:
            user = User.objects.get(pk=user_pk)
        except User.DoesNotExist:
            return _oauth_redirect_on_error("User not found.")

        user.github_token_encrypted = encrypt_token(gh_token)
        user.is_seller = True
        user.save(update_fields=["github_token_encrypted", "is_seller"])
        tokens = _issue_jwt(user)
        url = (
            f"{settings.FRONTEND_URL}/#auth/callback"
            f"&access={tokens['access']}&refresh={tokens['refresh']}"
        )
        return redirect(url)

    return _oauth_redirect_on_error(f"Unknown OAuth flow: '{flow}'.")


# ── profile ───────────────────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_my_repos(request):
    """
    GET /api/auth/github/repos/
    Fetch the authenticated user's GitHub repositories using their stored token.
    Returns a list of repos with name, full_name, description, private, html_url.
    """
    from .utils import decrypt_token

    user = request.user
    if not user.github_token_encrypted:
        return Response(
            {
                "detail": "GitHub repos not connected. Authorize first.",
                "connect_url": reverse("github_connect_repos"),
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        gh_token = decrypt_token(user.github_token_encrypted)
    except Exception:
        return Response(
            {"detail": "GitHub token is invalid. Please re-authorize."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/user/repos",
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/json"},
            params={"sort": "updated", "per_page": 100, "type": "owner"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return Response(
            {"detail": f"Failed to fetch repos from GitHub: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    repos = [
        {
            "full_name": r["full_name"],
            "name": r["name"],
            "description": r.get("description") or "",
            "private": r["private"],
            "html_url": r["html_url"],
            "language": r.get("language") or "",
            "updated_at": r.get("updated_at") or "",
            "topics": r.get("topics", []),
        }
        for r in resp.json()
        if not r.get("fork", False)
    ]

    return Response(repos)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def current_user(request):
    """
    GET  /api/auth/me/  — return the authenticated user's profile.
    PATCH /api/auth/me/ — update profile fields (bio, avatar_url, full_legal_name, phone_number).
    """
    if request.method == "GET":
        return Response(UserSerializer(request.user).data)

    serializer = UserSerializer(request.user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


# ── onboarding ────────────────────────────────────────────────────────────────


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def onboarding_submit(request):
    """
    POST /api/auth/onboarding/
    One-shot onboarding submission. Stores full_legal_name, phone_number,
    avatar_url, and records terms acceptance against the current version.
    Returns 400 if the user has already completed onboarding.
    """
    if request.user.is_onboarded:
        return Response(
            {"detail": "Onboarding is already complete."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = OnboardingSerializer(request.user, data=request.data)
    serializer.is_valid(raise_exception=True)
    from django.conf import settings as dj_settings

    # If no avatar_url was provided, keep the existing value (GitHub avatar).
    avatar = serializer.validated_data.get("avatar_url")
    if avatar is not None:
        request.user.avatar_url = avatar

    request.user.full_legal_name = serializer.validated_data["full_legal_name"]
    # Don't overwrite phone if it was already verified via Telegram
    submitted_phone = serializer.validated_data["phone_number"]
    if request.user.phone_verified and request.user.phone_number == submitted_phone:
        # Phone was verified via Telegram with the same number — keep verified status
        pass
    else:
        request.user.phone_number = submitted_phone
        # If they changed the phone number, reset verification
        if request.user.phone_number != submitted_phone:
            request.user.phone_verified = False
            request.user.phone_verified_at = None
    request.user.terms_accepted_version = dj_settings.CURRENT_TERMS_VERSION
    request.user.terms_accepted_at = timezone.now()
    request.user.save(
        update_fields=[
            "full_legal_name",
            "phone_number",
            "avatar_url",
            "phone_verified",
            "phone_verified_at",
            "terms_accepted_version",
            "terms_accepted_at",
        ]
    )

    return Response(UserSerializer(request.user).data)


# ── phone verification ────────────────────────────────────────────────────────

LINKING_TOKEN_RATE_LIMIT = 3  # max tokens per window
LINKING_TOKEN_RATE_WINDOW = 10 * 60  # 10 minutes in seconds
LINKING_TOKEN_EXPIRY_MINUTES = 10


def _check_linking_token_rate_limit(user_id: int) -> bool:
    """
    Check if the user has exceeded the rate limit for linking token generation.
    Returns True if rate-limited (should NOT generate a new token).
    If Redis is unavailable, degrades gracefully by allowing the request.
    """
    from django.core.cache import cache

    cache_key = f"linking_token_requests:{user_id}"
    try:
        current = cache.get(cache_key, 0)
        if current >= LINKING_TOKEN_RATE_LIMIT:
            return True
        cache.set(cache_key, current + 1, LINKING_TOKEN_RATE_WINDOW)
    except Exception:
        # Redis unavailable — skip rate limiting rather than crashing.
        pass
    return False


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def phone_link(request):
    """
    POST /api/auth/phone/link/
    Generate a single-use linking token and return the Telegram deep link.
    Rate-limited to 3 requests per 10 minutes per user.
    """
    user = request.user

    # Rate limit check
    if _check_linking_token_rate_limit(user.id):
        return Response(
            {"detail": "Too many link requests. Please wait a few minutes before trying again."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # Generate a new linking token
    from notifications.models import TelegramLinkingToken

    token = TelegramLinkingToken.objects.create(
        user=user,
        expires_at=timezone.now() + timezone.timedelta(minutes=LINKING_TOKEN_EXPIRY_MINUTES),
    )

    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "cooplink_bot")
    deep_link = f"https://t.me/{bot_username}?start={token.token}"

    return Response(
        {
            "deep_link": deep_link,
            "expires_at": token.expires_at.isoformat(),
            "token": str(token.token),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def phone_verify(request):
    """
    POST /api/auth/phone/verify/
    Verify a 6-digit code submitted by the user.
    Body: {"code": "123456"}

    Checks: code exists, belongs to user, not expired, not used,
    not exceeded max attempts. On success: marks phone as verified.
    """
    code = request.data.get("code", "").strip()

    if not code or len(code) != 6 or not code.isdigit():
        return Response(
            {"detail": "Please enter a valid 6-digit code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from notifications.models import PhoneVerificationCode

    # Find the most recent active code for this user
    verification_code = (
        PhoneVerificationCode.objects.filter(user=request.user, used=False)
        .order_by("-created_at")
        .first()
    )

    if verification_code is None:
        return Response(
            {"detail": "No active verification code found. Please request a new one via Telegram."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check expiry
    if verification_code.is_expired:
        return Response(
            {
                "detail": (
                    "This verification code has expired. Please request a new one via Telegram."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check max attempts
    if verification_code.attempts >= PhoneVerificationCode.MAX_ATTEMPTS:
        return Response(
            {"detail": "Too many failed attempts. Please request a new code via Telegram."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify the code
    if verification_code.code != code:
        # Record the failed attempt
        still_valid = verification_code.record_attempt()
        remaining = PhoneVerificationCode.MAX_ATTEMPTS - verification_code.attempts
        if not still_valid:
            return Response(
                {
                    "detail": (
                        "Too many failed attempts. This code has been invalidated. "
                        "Please request a new one."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": f"Invalid code. {remaining} attempts remaining."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check if this phone is already verified on another account
    phone_number = verification_code.phone_number
    if (
        User.objects.filter(phone_number=phone_number, phone_verified=True)
        .exclude(pk=request.user.pk)
        .exists()
    ):
        return Response(
            {"detail": "This phone number is already verified on another account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # SUCCESS: Mark the code as used and verify the phone
    verification_code.used = True
    verification_code.used_at = timezone.now()
    verification_code.save(update_fields=["used", "used_at"])

    request.user.phone_number = phone_number
    request.user.phone_verified = True
    request.user.phone_verified_at = timezone.now()
    request.user.save(update_fields=["phone_number", "phone_verified", "phone_verified_at"])

    return Response(
        {
            "detail": "Phone number verified successfully.",
            "phone_number": phone_number,
            "phone_verified": True,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def phone_status(request):
    """
    GET /api/auth/phone/status/
    Return the current phone verification status for the authenticated user.
    """
    from notifications.models import PhoneVerificationCode

    user = request.user

    # Check if there's an active (unexpired, unused) code
    active_code = (
        PhoneVerificationCode.objects.filter(user=user, used=False, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )

    return Response(
        {
            "phone_number": user.phone_number,
            "phone_verified": user.phone_verified,
            "phone_verified_at": user.phone_verified_at.isoformat()
            if user.phone_verified_at
            else None,
            "has_active_code": active_code is not None,
            "code_expires_at": active_code.expires_at.isoformat() if active_code else None,
        }
    )
