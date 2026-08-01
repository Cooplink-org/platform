import urllib.parse
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import BlacklistedToken, RefreshToken

from accounts.views import _FLOW_CONNECT_REPOS, _FLOW_LOGIN, _STATE_SALT, _build_signed_state
from notifications.models import PhoneVerificationCode, TelegramLinkingToken

User = get_user_model()

FRONTEND_URL = "http://localhost:3000"

# DRF caches throttle classes/rates on class-level attributes at import time,
# so @override_settings(REST_FRAMEWORK=…) cannot disable them.  Patch the
# base classes directly so every view in this file runs without throttling.
APIView.throttle_classes = []
SimpleRateThrottle.THROTTLE_RATES = {"anon": None, "user": None, "burst": None}


def _make_github_profile(github_id="123456", login="octocat", email="octo@example.com"):
    return {
        "id": int(github_id),
        "login": login,
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "email": email,
    }


def _bearer(user):
    return f"Bearer {RefreshToken.for_user(user).access_token}"


def _onboard(user):
    user.full_legal_name = "Test User"
    user.phone_number = "+998901234567"
    user.avatar_url = "https://avatars.githubusercontent.com/u/1"
    user.terms_accepted_version = settings.CURRENT_TERMS_VERSION
    user.terms_accepted_at = __import__("django").utils.timezone.now()
    user.save(
        update_fields=[
            "full_legal_name",
            "phone_number",
            "avatar_url",
            "terms_accepted_version",
            "terms_accepted_at",
        ]
    )
    return user


def _assert_redirects_to_frontend(resp, access_should_contain=None, error_should_contain=None):
    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    url = resp.url
    assert url.startswith(FRONTEND_URL), f"Unexpected redirect URL: {url}"
    parsed = urllib.parse.urlparse(url)
    assert url.startswith(f"{FRONTEND_URL}/#auth/callback"), (
        f"Expected fragment-based callback URL, got: {url}"
    )
    if access_should_contain:
        assert access_should_contain in parsed.fragment, (
            f"Expected '{access_should_contain}' in fragment '{parsed.fragment}'"
        )
    if error_should_contain:
        decoded = urllib.parse.unquote(parsed.fragment)
        assert error_should_contain in decoded, (
            f"Expected '{error_should_contain}' in fragment '{decoded}'"
        )


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
        resp = self.client.get(reverse("github_login"))
        url = resp.json()["authorization_url"]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        payload = signing.loads(state, salt=_STATE_SALT)
        self.assertEqual(payload["flow"], _FLOW_LOGIN)
        self.assertIn("nonce", payload)


# ── connect-repos view ────────────────────────────────────────────────────────


class GitHubConnectReposViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _onboard(
            User.objects.create_user(username="seller", email="seller@example.com")
        )
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))

    def test_returns_authorization_url(self):
        resp = self.client.get(reverse("github_connect_repos"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("authorization_url", data)
        url = data["authorization_url"]
        self.assertIn("public_repo", url)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        payload = signing.loads(state, salt=_STATE_SALT)
        self.assertEqual(payload["flow"], _FLOW_CONNECT_REPOS)
        self.assertEqual(payload["user_pk"], self.user.pk)

    def test_unauthenticated_returns_401(self):
        resp = APIClient().get(reverse("github_connect_repos"))
        self.assertEqual(resp.status_code, 401)


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

    def test_login_new_user_created(self):
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        with self._mock_exchange(), self._mock_profile(profile), self._mock_email():
            resp = self.client.get(reverse("github_callback"), {"code": "code123", "state": state})
        _assert_redirects_to_frontend(resp, access_should_contain="access=")
        self.assertTrue(User.objects.filter(github_id="123456").exists())

    def test_login_existing_user_updated(self):
        user = User.objects.create_user(
            username="octocat",
            email="octo@example.com",
            github_id="123456",
            github_username="octocat",
        )
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile(login="octocat-new", email="updated@example.com")
        with self._mock_exchange(), self._mock_profile(profile), self._mock_email():
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, access_should_contain="access=")
        user.refresh_from_db()
        self.assertEqual(user.github_username, "octocat-new")
        self.assertEqual(user.email, "updated@example.com")

    def test_login_username_collision_resolved(self):
        User.objects.create_user(username="octocat", email="other@example.com")
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        with (
            self._mock_exchange(),
            self._mock_profile(profile),
            self._mock_email("octo@example.com"),
        ):
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, access_should_contain="access=")
        new_user = User.objects.get(github_id="123456")
        self.assertNotEqual(new_user.username, "octocat")

    def test_connect_repos_stores_encrypted_token_and_sets_seller(self):
        user = User.objects.create_user(username="seller", email="seller@example.com")
        state = _build_signed_state(_FLOW_CONNECT_REPOS, user_pk=user.pk)
        with self._mock_exchange("gho_repo_token"):
            resp = self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        _assert_redirects_to_frontend(resp, access_should_contain="access=")
        user.refresh_from_db()
        self.assertTrue(user.is_seller)
        self.assertIsNotNone(user.github_token_encrypted)
        self.assertNotEqual(user.github_token_encrypted, "gho_repo_token")

    def test_connect_repos_decrypts_correctly(self):
        from accounts.utils import decrypt_token

        user = User.objects.create_user(username="seller2", email="seller2@example.com")
        state = _build_signed_state(_FLOW_CONNECT_REPOS, user_pk=user.pk)
        with self._mock_exchange("gho_secret_token"):
            self.client.get(reverse("github_callback"), {"code": "code", "state": state})
        user.refresh_from_db()
        self.assertEqual(decrypt_token(user.github_token_encrypted), "gho_secret_token")

    def test_missing_code_returns_redirect_with_error(self):
        resp = self.client.get(reverse("github_callback"))
        _assert_redirects_to_frontend(resp, error_should_contain="Missing code")

    def test_missing_state_returns_redirect_with_error(self):
        resp = self.client.get(reverse("github_callback"), {"code": "code123"})
        _assert_redirects_to_frontend(resp, error_should_contain="Missing code")

    def test_invalid_state_signature_returns_redirect_with_error(self):
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
        with patch(
            "accounts.views._exchange_code_for_token", side_effect=req.RequestException("timeout")
        ):
            resp = self.client.get(reverse("github_callback"), {"code": "bad", "state": state})
        _assert_redirects_to_frontend(resp, error_should_contain="GitHub token exchange failed")

    def test_token_exchange_includes_state(self):
        state = _build_signed_state(_FLOW_LOGIN)
        profile = _make_github_profile()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"access_token": "gho_test"}
        with (
            patch("accounts.views.requests.post", return_value=mock_resp) as mock_post,
            self._mock_profile(profile),
            self._mock_email(),
        ):
            resp = self.client.get(reverse("github_callback"), {"code": "code123", "state": state})
        _assert_redirects_to_frontend(resp, access_should_contain="access=")
        _, kwargs = mock_post.call_args
        self.assertNotIn("redirect_uri", kwargs["data"])
        self.assertEqual(kwargs["data"]["state"], state)
        self.assertNotIn("json", kwargs)

    def test_callback_missing_both_code_and_state(self):
        resp = self.client.get(reverse("github_callback"))
        _assert_redirects_to_frontend(resp, error_should_contain="Missing code")


# ── /api/auth/me/ ─────────────────────────────────────────────────────────────


class CurrentUserViewTest(TestCase):
    def setUp(self):
        self.user = _onboard(
            User.objects.create_user(
                username="testuser",
                email="test@example.com",
                bio="Hello",
            )
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("current_user")

    def test_get_profile(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "testuser")

    def test_get_profile_includes_is_onboarded(self):
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertIn("is_onboarded", data)
        self.assertTrue(data["is_onboarded"])

    def test_patch_bio(self):
        resp = self.client.patch(self.url, {"bio": "Updated bio"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "Updated bio")

    def test_patch_avatar(self):
        resp = self.client.patch(
            self.url, {"avatar_url": "https://example.com/avatar.png"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["avatar_url"], "https://example.com/avatar.png")

    def test_patch_readonly_fields_ignored(self):
        self.user.github_username = "original"
        self.user.save()
        resp = self.client.patch(self.url, {"github_username": "hacked"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.github_username, "original")

    def test_unauthenticated_returns_401(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_get_profile_has_expected_fields(self):
        resp = self.client.get(self.url)
        data = resp.json()
        expected = {
            "id",
            "username",
            "email",
            "github_id",
            "github_username",
            "avatar_url",
            "bio",
            "is_seller",
            "telegram_chat_id",
            "full_legal_name",
            "phone_number",
            "phone_verified",
            "phone_verified_at",
            "terms_accepted_version",
            "terms_accepted_at",
            "is_onboarded",
            "is_staff",
            "created_at",
        }
        self.assertEqual(set(data.keys()), expected)


# ── onboarding ────────────────────────────────────────────────────────────────


class OnboardingSubmitTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="newuser",
            email="new@example.com",
            avatar_url="https://avatars.githubusercontent.com/u/1",
        )
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("onboarding_submit")
        self.valid_data = {
            "full_legal_name": "John Doe",
            "phone_number": "+998901234567",
            "avatar_url": "https://example.com/avatar.png",
            "terms_accepted": True,
        }

    def test_onboarding_completes_successfully(self):
        resp = self.client.post(self.url, self.valid_data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_onboarded)
        self.assertEqual(self.user.full_legal_name, "John Doe")
        self.assertEqual(self.user.phone_number, "+998901234567")
        self.assertEqual(self.user.terms_accepted_version, "2025-07-v1")

    def test_onboarding_sets_avatar_from_github_if_not_provided(self):
        data = {k: v for k, v in self.valid_data.items() if k != "avatar_url"}
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar_url, "https://avatars.githubusercontent.com/u/1")

    def test_onboarding_returns_400_if_already_onboarded(self):
        _onboard(self.user)
        resp = self.client.post(self.url, self.valid_data, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already complete", resp.json()["detail"].lower())

    def test_onboarding_requires_terms_accepted(self):
        data = {**self.valid_data, "terms_accepted": False}
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_onboarding_requires_full_legal_name(self):
        data = {**self.valid_data, "full_legal_name": ""}
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_onboarding_requires_phone_number(self):
        data = {**self.valid_data, "phone_number": ""}
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_onboarding_rejects_invalid_phone(self):
        data = {**self.valid_data, "phone_number": "abc"}
        resp = self.client.post(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_onboarding_requires_auth(self):
        resp = APIClient().post(self.url, self.valid_data, format="json")
        self.assertEqual(resp.status_code, 401)


# ── Onboarding gate (middleware enforced) ─────────────────────────────────────


class OnboardingGateMiddlewareTest(TestCase):
    """Test that non-exempt endpoints are blocked for incomplete profiles."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="incomplete", email="inc@example.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))

    def _assert_blocked(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("onboarding_required", resp.json())

    def test_blocked_from_listings(self):
        self._assert_blocked(reverse("public_project_list"))

    def test_blocked_from_dashboard_summary(self):
        self._assert_blocked(reverse("dashboard-summary"))

    def test_blocked_from_dashboard_sales(self):
        self._assert_blocked(reverse("dashboard-sales"))

    def test_blocked_from_dashboard_listings(self):
        self._assert_blocked(reverse("dashboard-listings"))

    def test_blocked_from_dashboard_earnings(self):
        self._assert_blocked(reverse("dashboard-earnings-timeseries"))

    def test_blocked_from_payouts(self):
        self._assert_blocked(reverse("payout_list_mine"))

    def test_blocked_from_orders(self):
        self._assert_blocked(reverse("order_create"))

    def test_blocked_from_moderation_reports(self):
        self._assert_blocked(reverse("create_report"))

    def test_blocked_from_listings_my_repos(self):
        self._assert_blocked(reverse("my_repos"))

    def test_blocked_from_listings_projects(self):
        self._assert_blocked(reverse("project_list_create"))

    def test_auth_endpoints_exempt(self):
        """Onboarding gate should not block /api/auth/ endpoints."""
        resp = self.client.get(reverse("current_user"))
        self.assertEqual(resp.status_code, 200)

    def test_onboarding_endpoint_exempt(self):
        resp = self.client.post(
            reverse("onboarding_submit"),
            {
                "full_legal_name": "John",
                "phone_number": "+998901234567",
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_token_endpoints_exempt(self):
        resp = self.client.post(reverse("token_obtain_pair"), {}, format="json")
        self.assertNotEqual(resp.status_code, 403)

    def test_anonymous_users_not_blocked(self):
        """Anonymous users should pass through the gate."""
        resp = APIClient().get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)

    def test_onboarded_users_not_blocked(self):
        _onboard(self.user)
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)

    def test_terms_version_bump_blocks_again(self):
        """Bumping CURRENT_TERMS_VERSION forces re-acceptance."""
        _onboard(self.user)
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)

        with override_settings(CURRENT_TERMS_VERSION="2025-08-v2"):
            self.user.terms_accepted_version = "2025-07-v1"
            self.user.save(update_fields=["terms_accepted_version"])
            resp = self.client.get(reverse("public_project_list"))
            self.assertEqual(resp.status_code, 403)
            self.assertIn("onboarding_required", resp.json())


# ── Token refresh rotation ────────────────────────────────────────────────────


class TokenRefreshRotationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _onboard(
            User.objects.create_user(
                username="tokenuser",
                email="token@example.com",
            )
        )
        self.refresh = RefreshToken.for_user(self.user)
        self.refresh_url = reverse("token_refresh")

    def test_refresh_issues_new_access_token(self):
        resp = self.client.post(self.refresh_url, {"refresh": str(self.refresh)}, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access", data)

    def test_refresh_rotation_issues_new_refresh_token(self):
        old_token_str = str(self.refresh)
        resp = self.client.post(self.refresh_url, {"refresh": old_token_str}, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("refresh", data)
        self.assertNotEqual(data["refresh"], old_token_str)

    def test_old_refresh_token_blacklisted_after_rotation(self):
        """After rotation, the old refresh token must be blacklisted (security fix)."""
        old_token_str = str(self.refresh)
        resp1 = self.client.post(self.refresh_url, {"refresh": old_token_str}, format="json")
        self.assertEqual(resp1.status_code, 200)

        # The old token should now be rejected
        resp2 = self.client.post(self.refresh_url, {"refresh": old_token_str}, format="json")
        self.assertEqual(resp2.status_code, 401)

    def test_invalid_refresh_rejected(self):
        resp = self.client.post(self.refresh_url, {"refresh": "totally-fake-token"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_expired_refresh_rejected(self):
        with patch(
            "rest_framework_simplejwt.tokens.RefreshToken.lifetime",
            new=__import__("datetime").timedelta(seconds=0),
        ):
            expired = RefreshToken.for_user(self.user)
        import time

        time.sleep(1)
        resp = self.client.post(self.refresh_url, {"refresh": str(expired)}, format="json")
        self.assertEqual(resp.status_code, 401)


# ── Token blacklist on ban (moderation integration) ───────────────────────────


class TokenBlacklistOnBanTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.target = User.objects.create_user(
            username="target",
            email="target@example.com",
        )
        self.target_token = RefreshToken.for_user(self.target)

    def test_ban_blacklists_outstanding_tokens(self):
        self.assertFalse(
            BlacklistedToken.objects.filter(token__jti=self.target_token.payload["jti"]).exists()
        )

        admin = User.objects.create_superuser(username="admin", password="pass")
        self.client.force_authenticate(admin)

        resp = self.client.post(reverse("admin_ban_user", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=self.target_token.payload["jti"]).exists()
        )

        self.assertFalse(User.objects.get(pk=self.target.pk).is_active)


# ── phone verification ────────────────────────────────────────────────────────


class PhoneLinkTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="phoneuser",
            email="phone@example.com",
            full_legal_name="Phone User",
            phone_number="+15550000000",
        )
        self.user.terms_accepted_version = "2025-07-v1"
        self.user.terms_accepted_at = timezone.now()
        self.user.save()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("phone_link")

    def test_requires_auth(self):
        resp = APIClient().post(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_generates_linking_token(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("deep_link", data)
        self.assertIn("expires_at", data)
        self.assertIn("token", data)
        self.assertTrue(data["deep_link"].startswith("https://t.me/"))

    def test_rate_limited_after_3_requests(self):
        for _ in range(3):
            resp = self.client.post(self.url)
            self.assertEqual(resp.status_code, 200)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 429)

    def test_token_stored_in_db(self):
        self.client.post(self.url)
        self.assertEqual(TelegramLinkingToken.objects.filter(user=self.user).count(), 1)


class PhoneVerifyTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="verifyuser",
            email="verify@example.com",
            full_legal_name="Verify User",
            phone_number="+15550000000",
        )
        self.user.terms_accepted_version = "2025-07-v1"
        self.user.terms_accepted_at = timezone.now()
        self.user.save()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("phone_verify")

        # Create a valid verification code
        self.code = PhoneVerificationCode.objects.create(
            user=self.user,
            code="123456",
            phone_number="+15551234567",
            telegram_chat_id="12345",
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )

    def test_requires_auth(self):
        resp = APIClient().post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 401)

    def test_rejects_empty_code(self):
        resp = self.client.post(self.url, {"code": ""})
        self.assertEqual(resp.status_code, 400)

    def test_rejects_short_code(self):
        resp = self.client.post(self.url, {"code": "12345"})
        self.assertEqual(resp.status_code, 400)

    def test_rejects_non_numeric_code(self):
        resp = self.client.post(self.url, {"code": "abcdef"})
        self.assertEqual(resp.status_code, 400)

    def test_valid_code_verifies_phone(self):
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["phone_verified"])
        self.assertEqual(data["phone_number"], "+15551234567")

        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)
        self.assertEqual(self.user.phone_number, "+15551234567")

    def test_wrong_code_rejected(self):
        resp = self.client.post(self.url, {"code": "654321"})
        self.assertEqual(resp.status_code, 400)
        self.code.refresh_from_db()
        self.assertEqual(self.code.attempts, 1)
        self.assertFalse(self.code.used)

    def test_max_attempts_invalidates_code(self):
        for _ in range(5):
            resp = self.client.post(self.url, {"code": "000000"})
        self.code.refresh_from_db()
        self.assertTrue(self.code.used)

        # Even correct code should be rejected now
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 400)

    def test_expired_code_rejected(self):
        self.code.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.code.save()
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("expired", resp.json()["detail"].lower())

    def test_code_cannot_be_reused(self):
        self.client.post(self.url, {"code": "123456"})
        # Try again with the same code
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no active", resp.json()["detail"].lower())

    def test_already_verified_phone_on_another_account_rejected(self):
        # Verify the phone on another user first
        User.objects.create_user(
            username="other",
            email="other@example.com",
            phone_number="+15551234567",
            phone_verified=True,
        )
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already verified", resp.json()["detail"].lower())

    def test_no_active_code_returns_error(self):
        # Delete the code
        self.code.delete()
        resp = self.client.post(self.url, {"code": "123456"})
        self.assertEqual(resp.status_code, 400)


class PhoneStatusTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="statususer",
            email="status@example.com",
            full_legal_name="Status User",
            phone_number="+15550000000",
        )
        self.user.terms_accepted_version = "2025-07-v1"
        self.user.terms_accepted_at = timezone.now()
        self.user.save()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        self.url = reverse("phone_status")

    def test_requires_auth(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_unverified_status(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["phone_verified"])
        self.assertFalse(data["has_active_code"])

    def test_verified_status(self):
        self.user.phone_verified = True
        self.user.phone_verified_at = timezone.now()
        self.user.save()
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertTrue(data["phone_verified"])
        self.assertIsNotNone(data["phone_verified_at"])

    def test_active_code_detected(self):
        PhoneVerificationCode.objects.create(
            user=self.user,
            code="123456",
            phone_number="+15559999999",
            telegram_chat_id="99999",
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertTrue(data["has_active_code"])
        self.assertIsNotNone(data["code_expires_at"])


class TelegramLinkingTokenModelTest(TestCase):
    def test_valid_token(self):
        user = User.objects.create_user(username="t1")
        token = TelegramLinkingToken.objects.create(
            user=user,
            expires_at=timezone.now() + timezone.timedelta(minutes=10),
        )
        self.assertTrue(token.is_valid)
        self.assertFalse(token.is_expired)
        self.assertFalse(token.consumed)

    def test_expired_token(self):
        user = User.objects.create_user(username="t2")
        token = TelegramLinkingToken.objects.create(
            user=user,
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.assertFalse(token.is_valid)
        self.assertTrue(token.is_expired)

    def test_consumed_token(self):
        user = User.objects.create_user(username="t3")
        token = TelegramLinkingToken.objects.create(
            user=user,
            expires_at=timezone.now() + timezone.timedelta(minutes=10),
            consumed=True,
        )
        self.assertFalse(token.is_valid)


class PhoneVerificationCodeModelTest(TestCase):
    def test_valid_code(self):
        user = User.objects.create_user(username="c1")
        code = PhoneVerificationCode.objects.create(
            user=user,
            code="123456",
            phone_number="+15551111111",
            telegram_chat_id="111",
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        self.assertTrue(code.is_valid)

    def test_expired_code(self):
        user = User.objects.create_user(username="c2")
        code = PhoneVerificationCode.objects.create(
            user=user,
            code="123456",
            phone_number="+15551111111",
            telegram_chat_id="111",
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.assertFalse(code.is_valid)

    def test_record_attempt_increments(self):
        user = User.objects.create_user(username="c3")
        code = PhoneVerificationCode.objects.create(
            user=user,
            code="123456",
            phone_number="+15551111111",
            telegram_chat_id="111",
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        for i in range(4):
            result = code.record_attempt()
            self.assertTrue(result)
            self.assertEqual(code.attempts, i + 1)

        # 5th attempt should invalidate
        result = code.record_attempt()
        self.assertFalse(result)
        self.assertTrue(code.used)


class UniqueVerifiedPhoneTest(TestCase):
    def test_unique_constraint_on_verified_phone(self):
        User.objects.create_user(
            username="u1",
            phone_number="+15550001111",
            phone_verified=True,
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="u2",
                phone_number="+15550001111",
                phone_verified=True,
            )

    def test_same_phone_unverified_is_allowed(self):
        User.objects.create_user(
            username="u3",
            phone_number="+15550002222",
            phone_verified=False,
        )
        # Should not raise
        User.objects.create_user(
            username="u4",
            phone_number="+15550002222",
            phone_verified=False,
        )


# ── Security regression tests ─────────────────────────────────────────────────


class BannedUserJWTTest(TestCase):
    """Regression: banned users must be rejected on every authenticated request."""

    def setUp(self):
        self.client = APIClient()
        self.user = _onboard(User.objects.create_user(username="bannable", email="ban@example.com"))

    def test_banned_user_cannot_access_authenticated_endpoints(self):
        # Get a valid token before banning
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)

        # Ban the user
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        # Try to use the still-valid access token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        resp = self.client.get(reverse("current_user"))

        # Should be rejected (401 from InvalidToken)
        self.assertIn(resp.status_code, [401, 403])

    def test_banned_user_refresh_token_blacklisted(self):
        """When a user is banned, all outstanding refresh tokens are blacklisted."""
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        refresh = RefreshToken.for_user(self.user)

        # Ban the user (same logic as admin_ban_user view)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        for token in OutstandingToken.objects.filter(user=self.user):
            BlacklistedToken.objects.get_or_create(token=token)

        # Try to refresh — should fail
        self.client.credentials()
        resp = self.client.post(
            reverse("token_refresh"),
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertIn(resp.status_code, [401, 403])


class RefreshTokenRotationBlacklistTest(TestCase):
    """Regression: BLACKLIST_AFTER_ROTATION=True must blacklist old refresh tokens."""

    def test_old_refresh_token_blacklisted_after_rotation(self):
        user = _onboard(User.objects.create_user(username="rotator", email="rot@example.com"))
        refresh = RefreshToken.for_user(user)
        old_refresh_str = str(refresh)
        old_jti = refresh["jti"]

        # Use the refresh token to get a new one (rotation)
        client = APIClient()
        resp = client.post(
            reverse("token_refresh"),
            {"refresh": old_refresh_str},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # The old refresh token's JTI should now be in the blacklist
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=old_jti).exists(),
            "Old refresh token should be blacklisted after rotation",
        )

        # Trying to use the old refresh token should fail
        resp2 = client.post(
            reverse("token_refresh"),
            {"refresh": old_refresh_str},
            format="json",
        )
        self.assertIn(resp2.status_code, [401, 403])


class OnboardingGateTest(TestCase):
    """Regression: onboarding gate must block un-onboarded users from API."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="unboarded", email="unb@example.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))

    def test_unonboarded_user_blocked_from_api(self):
        # The /api/auth/me/ endpoint is exempt (under /api/auth/)
        # But /api/listings/projects/ should be blocked
        resp = self.client.get("/api/listings/projects/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json().get("onboarding_required", False))


class OnboardingBypassPreventionTest(TestCase):
    """Regression: users must not bypass onboarding by PATCHing protected fields directly."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="bypass_test", email="bypass@example.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))

    def test_cannot_set_terms_accepted_via_patch(self):
        """terms_accepted_version and terms_accepted_at must be read-only."""
        resp = self.client.patch(
            reverse("current_user"),
            {
                "terms_accepted_version": settings.CURRENT_TERMS_VERSION,
                "terms_accepted_at": "2025-01-01T00:00:00Z",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        # Fields should remain unchanged
        self.assertEqual(self.user.terms_accepted_version, "")
        self.assertIsNone(self.user.terms_accepted_at)

    def test_cannot_set_telegram_chat_id_via_patch(self):
        """telegram_chat_id must be read-only — only set via Telegram linking flow."""
        resp = self.client.patch(
            reverse("current_user"),
            {"telegram_chat_id": "123456789"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.telegram_chat_id)
