import secrets
import urllib.parse
import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import UserSerializer
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
def github_login(request):
    """
    GET /api/auth/github/login/
    Returns the GitHub authorization URL for the login flow.
    Scope: read:user user:email only — repo access is a separate incremental step.

    The `state` parameter is a signed token verified on callback (no session cookie
    required — OAuth redirects from GitHub often drop SameSite session cookies).
    """
    state = _build_signed_state(_FLOW_LOGIN)

    params = urllib.parse.urlencode({
        "client_id": settings.GITHUB_CLIENT_ID,
        "scope": "read:user user:email",
        "state": state,
    })
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

    params = urllib.parse.urlencode({
        "client_id": settings.GITHUB_CLIENT_ID,
        "scope": "public_repo",
        "state": state,
    })
    return Response({"authorization_url": f"{GITHUB_AUTHORIZE_URL}?{params}"})


# ── unified callback ──────────────────────────────────────────────────────────

def _oauth_redirect_on_error(message: str, status_code: int = 400) -> Response | None:
    """
    Redirect the browser to the frontend with an error parameter.
    Used when the OAuth callback encounters a failure.
    """
    from_url = settings.FRONTEND_URL
    url = f"{from_url}/auth/callback?error={urllib.parse.quote(message)}"
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
        url = f"{settings.FRONTEND_URL}/#auth/callback&access={tokens['access']}&refresh={tokens['refresh']}"
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
        url = f"{settings.FRONTEND_URL}/#auth/callback&access={tokens['access']}&refresh={tokens['refresh']}"
        return redirect(url)

    return _oauth_redirect_on_error(f"Unknown OAuth flow: '{flow}'.")


# ── profile ───────────────────────────────────────────────────────────────────

@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def current_user(request):
    """
    GET  /api/auth/me/  — return the authenticated user's profile.
    PATCH /api/auth/me/ — update bio and/or avatar_url.
    """
    if request.method == "GET":
        return Response(UserSerializer(request.user).data)

    serializer = UserSerializer(request.user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)
