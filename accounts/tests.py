"""
Unit tests for the accounts app.

Run with: uv run manage.py test accounts
"""
import urllib.parse
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.views import _FLOW_CONNECT_REPOS, _FLOW_LOGIN, _STATE_SALT, _build_signed_state

User = get_user_model()

FRONTEND_URL = "http://localhost:3000"


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_github_profile(github_id="123456", login="octocat", email="octo@example.com"):
    return {
        "id": int(github_id),
        "login": login,
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "email": email,
    }


def _bearer(user):
    return f"Bearer {RefreshToken.for_user(user).access_token}"


def _assert_redirects_to_frontend(resp, fragment_should_contain=None, error_should_contain=None):
    """Assert the response is a redirect to the frontend /auth/callback."""
    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    url = resp.url
    assert url.startswith(f"{FRONTEND_URL}/auth/callback"), f"Unexpected redirect URL: {url}"
    parsed = urllib.parse.urlparse(url)
    if fragment_should_contain:
        assert fragment_should_contain in parsed.fragment, \
            f"Expected '{fragment_should_contain}' in fragment '{parsed.fragment}'"
    if error_should_contain:
        qs = urllib.parse.parse_qs(parsed.query)
        error_param = qs.get("error", [""])[0]
        assert error_should_contain in error_param, \
            f"Expected '{error_should_contain}' in error '{error_param}'"


# ── GitHub login redirect ─────────────────────────────────────────────────────

class GitHubLoginViewTest(TestCase):
    def test_returns_authorization_url(self):
        resp = self.client.get(reverse("github_login"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("authorization_url", data)
        url = data["authorization_url"]
        self.assertIn("github.com/login/oauth/authorize", url)
        self.assertIn("read%3Auser", url)
        self.assertIn("user%3Aemail", url)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        payload = signing.loads(state, salt=_STATE_SALT)
        self.assertEqual(payload["flow"], _FLOW_LOGIN)

    def test_state_is_signed_opaque_token(self):
        """State must be a signed opaque token verifiable without session cookies."""
        resp = self.client.get(reverse("github_login"))
        url = resp.json()["authorization_url"]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        payload = signing.loads(state, salt=_STATE_SALT)
        self.assertEqual(payload["flow"], _FLOW_LOGIN)
        self.assertIn("nonce", payload)


# ── GitHub unified callback ───────────────────────────────────────────────────

@override_settings(FRONTEND_URL=FRONTEND_URL)
class GitHubCallbackViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _mock_exchange(self, token="gho_test_token"):
        return patch("accounts.views._exchange_code_for_token", return_value=token)

    def _mock_profile(self, profile):
        return patch("accounts.views._fetch_github_profile", return_value=profile)

    def _mock_email(self, email=None):
        return patch("accounts.views._fetch_github_primary_email", return_value=email)

    # ── login flow ────────────────────────────────────────────────────────────

    def test_login_new_user_created(self):
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        with self._mock_exchange(), self._mock_profile(profile), self._mock_email():
            resp = self.client.get(reverse("github_callback"), {"code": "code123", "state": state})
        _assert_redirects_to_frontend(resp, fragment_should_contain="access=")
        self.assertTrue(User.objects.filter(github_id="123456").exists())

    def test_login_existing_user_updated(self):
        user = User.objects.create_user(
            username="octocat", email="octo@example.com",
            github_id="123456", github_username="octocat",
        )
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile(login="octocat-new", email="updated@example.com")
        with self._mock_exchange(), self._mock_profile(profile), self._mock_email():
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, fragment_should_contain="access=")
        user.refresh_from_db()
        self.assertEqual(user.github_username, "octocat-new")
        self.assertEqual(user.email, "updated@example.com")

    def test_login_username_collision_resolved(self):
        User.objects.create_user(username="octocat", email="other@example.com")
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        with self._mock_exchange(), self._mock_profile(profile), self._mock_email("octo@example.com"):
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, fragment_should_contain="access=")
        new_user = User.objects.get(github_id="123456")
        self.assertNotEqual(new_user.username, "octocat")

    # ── connect_repos flow ────────────────────────────────────────────────────

    def test_connect_repos_stores_encrypted_token_and_sets_seller(self):
        user = User.objects.create_user(username="seller", email="seller@example.com")
        state = _build_signed_state(_FLOW_CONNECT_REPOS, user_pk=user.pk)

        with self._mock_exchange("gho_repo_token"):
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})

        _assert_redirects_to_frontend(resp, fragment_should_contain="access=")
        user.refresh_from_db()
        self.assertTrue(user.is_seller)
        self.assertIsNotNone(user.github_token_encrypted)
        # Confirm the token is actually encrypted (not stored in plaintext)
        self.assertNotEqual(user.github_token_encrypted, "gho_repo_token")

    def test_connect_repos_decrypts_correctly(self):
        from accounts.utils import decrypt_token
        user = User.objects.create_user(username="seller2", email="seller2@example.com")
        state = _build_signed_state(_FLOW_CONNECT_REPOS, user_pk=user.pk)

        with self._mock_exchange("gho_secret_token"):
            self.client.get(reverse("github_callback"), {"code": "code", "state": state})

        user.refresh_from_db()
        self.assertEqual(decrypt_token(user.github_token_encrypted), "gho_secret_token")

    # ── error cases (redirect to frontend with ?error=) ───────────────────────

    def test_missing_code_returns_redirect_with_error(self):
        resp = self.client.get(reverse("github_callback"))
        _assert_redirects_to_frontend(resp, error_should_contain="Missing code")

    def test_missing_state_returns_redirect_with_error(self):
        resp = self.client.get(reverse("github_callback"), {"code": "code123"})
        _assert_redirects_to_frontend(resp, error_should_contain="Missing code")

    def test_invalid_state_signature_returns_redirect_with_error(self):
        """Tampered or unsigned state must be rejected."""
        with self._mock_exchange(), self._mock_profile(_make_github_profile()), self._mock_email():
            resp = self.client.get(
                reverse("github_callback"),
                {"code": "code", "state": "login:definitely-not-signed"},
            )
        _assert_redirects_to_frontend(resp, error_should_contain="Invalid state")

    def test_unknown_flow_returns_redirect_with_error(self):
        state = signing.dumps({"flow": "badflow", "nonce": "x"}, salt=_STATE_SALT)
        with self._mock_exchange():
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, error_should_contain="Unknown OAuth flow")

    def test_github_exchange_failure_returns_redirect_with_error(self):
        import requests as req
        state = _build_signed_state(_FLOW_LOGIN)
        with patch("accounts.views._exchange_code_for_token", side_effect=req.RequestException("timeout")):
            resp = self.client.get(reverse("github_callback"), {"code": "bad", "state": state})
        _assert_redirects_to_frontend(resp, error_should_contain="GitHub token exchange failed")

    def test_token_exchange_includes_state(self):
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"access_token": "gho_test"}
        with patch("accounts.views.requests.post", return_value=mock_resp) as mock_post, \
                self._mock_profile(profile), self._mock_email():
            resp = self.client.get(reverse("github_callback"), {"code": "code123", "state": state})
        _assert_redirects_to_frontend(resp, fragment_should_contain="access=")
        _, kwargs = mock_post.call_args
        # We intentionally omit redirect_uri — GitHub uses the registered callback URL
        self.assertNotIn("redirect_uri", kwargs["data"])
        self.assertEqual(kwargs["data"]["state"], state)
        self.assertNotIn("json", kwargs)


# ── /api/auth/me/ ─────────────────────────────────────────────────────────────

class CurrentUserViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", bio="Hello",
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("current_user")

    def test_get_profile(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "testuser")

    def test_patch_bio(self):
        resp = self.client.patch(self.url, {"bio": "Updated bio"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "Updated bio")

    def test_patch_avatar(self):
        resp = self.client.patch(self.url, {"avatar_url": "https://example.com/avatar.png"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["avatar_url"], "https://example.com/avatar.png")

    def test_patch_readonly_fields_ignored(self):
        """Read-only fields like github_username must not change via PATCH."""
        self.user.github_username = "original"
        self.user.save()
        resp = self.client.patch(self.url, {"github_username": "hacked"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.github_username, "original")

    def test_unauthenticated_returns_401(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)
