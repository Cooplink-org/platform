from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from listings.models import Category, Project

from .models import ModerationLog, Report

User = get_user_model()

APIView.throttle_classes = []
SimpleRateThrottle.THROTTLE_RATES = {"anon": None, "user": None, "burst": None}


def _bearer(user):
    return f"Bearer {RefreshToken.for_user(user).access_token}"


def _onboard(user):
    user.full_legal_name = "Test User"
    user.phone_number = "+998901234567"
    user.avatar_url = "https://avatars.githubusercontent.com/u/1"
    user.terms_accepted_version = settings.CURRENT_TERMS_VERSION
    user.terms_accepted_at = timezone.now()
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


def _seller(**kwargs):
    u = User.objects.create_user(is_seller=True, **kwargs)
    u.github_token_encrypted = "gAAAAABmocked=="
    u.save(update_fields=["github_token_encrypted"])
    return _onboard(u)


def _buyer(**kwargs):
    return _onboard(User.objects.create_user(**kwargs))


def _admin(**kwargs):
    u = User.objects.create_superuser(is_staff=True, is_superuser=True, **kwargs)
    return _onboard(u)


def _published_project(seller, title="Mod Proj"):
    cat, _ = Category.objects.get_or_create(name="Mod", defaults={"slug": "mod"})
    return Project.objects.create(
        title=title,
        slug=title.lower().replace(" ", "-"),
        description="d",
        price="10000.00",
        status=Project.Status.PUBLISHED,
        seller=seller,
        category=cat,
    )


# ── user-facing reports ───────────────────────────────────────────────────────


class CreateReportTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reporter = _buyer(username="reporter", email="rep@test.com")
        self.target_user = _buyer(username="target_user", email="tgt@test.com")
        self.seller_user = _seller(username="target_seller", email="ts@test.com")
        self.project = _published_project(self.seller_user)
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.reporter))
        self.url = reverse("create_report")

    def test_report_project(self):
        resp = self.client.post(
            self.url,
            {
                "project": self.project.id,
                "reason": "malicious_code",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["reason"], "malicious_code")
        self.assertEqual(resp.json()["project"], self.project.id)

    def test_report_user(self):
        resp = self.client.post(
            self.url,
            {
                "reported_user": self.target_user.id,
                "reason": "fraud",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["reported_user"], self.target_user.id)

    def test_report_with_detail(self):
        resp = self.client.post(
            self.url,
            {
                "project": self.project.id,
                "reason": "other",
                "detail": "Suspicious activity",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["detail"], "Suspicious activity")

    def test_both_project_and_user_rejected(self):
        resp = self.client.post(
            self.url,
            {
                "project": self.project.id,
                "reported_user": self.target_user.id,
                "reason": "spam",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Exactly one", resp.json()["detail"])

    def test_neither_project_nor_user_rejected(self):
        resp = self.client.post(self.url, {"reason": "spam"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Exactly one", resp.json()["detail"])

    def test_self_report_project_blocked(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller_user))
        resp = self.client.post(
            self.url,
            {
                "project": self.project.id,
                "reason": "copyright",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot report", resp.json()["detail"].lower())

    def test_self_report_user_blocked(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.target_user))
        resp = self.client.post(
            self.url,
            {
                "reported_user": self.target_user.id,
                "reason": "inappropriate",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot report", resp.json()["detail"].lower())

    def test_requires_auth(self):
        resp = APIClient().post(
            self.url,
            {
                "project": self.project.id,
                "reason": "spam",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_report_serializer_includes_reporter_info(self):
        resp = self.client.post(
            self.url,
            {
                "project": self.project.id,
                "reason": "spam",
            },
            format="json",
        )
        data = resp.json()
        self.assertIn("reporter_username", data)
        self.assertEqual(data["reporter_username"], "reporter")


# ── my reports list ───────────────────────────────────────────────────────────


class MyReportsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _buyer(username="myrep", email="mr@test.com")
        self.seller = _seller(username="mr_seller", email="mrs@test.com")
        self.project = _published_project(self.seller)
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        Report.objects.create(
            reporter=self.user,
            project=self.project,
            reason="spam",
        )
        self.url = reverse("my_reports")

    def test_lists_own_reports(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 1)

    def test_does_not_show_other_reports(self):
        other = _buyer(username="other_rep", email="or@test.com")
        Report.objects.create(reporter=other, project=self.project, reason="fraud")
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.json()["results"]), 1)


# ── admin report list ─────────────────────────────────────────────────────────


class AdminReportListTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _admin(username="adm", email="adm@test.com")
        self.user = _buyer(username="ar_user", email="aru@test.com")
        self.seller = _seller(username="ar_seller", email="ars@test.com")
        self.project = _published_project(self.seller)
        Report.objects.create(reporter=self.user, project=self.project, reason="malicious_code")
        self.url = reverse("admin_report_list")

    def test_staff_can_list_reports(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 1)

    def test_non_staff_gets_403(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_filter_by_status(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))
        resp = self.client.get(f"{self.url}?status=pending")
        self.assertEqual(len(resp.json()["results"]), 1)
        resp = self.client.get(f"{self.url}?status=reviewed")
        self.assertEqual(len(resp.json()["results"]), 0)


# ── admin report update ───────────────────────────────────────────────────────


class AdminReportUpdateTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _admin(username="adm_upd", email="admu@test.com")
        self.user = _buyer(username="aru2", email="aru2@test.com")
        self.seller = _seller(username="ars2", email="ars2@test.com")
        self.project = _published_project(self.seller)
        self.report = Report.objects.create(
            reporter=self.user,
            project=self.project,
            reason="copyright",
        )
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))
        self.url = reverse("admin_report_update", args=[self.report.pk])

    def test_update_report_status(self):
        resp = self.client.patch(self.url, {"status": "reviewed"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "reviewed")

    def test_update_creates_moderation_log(self):
        self.client.patch(self.url, {"status": "dismissed", "reason": "No evidence"}, format="json")
        self.assertTrue(
            ModerationLog.objects.filter(
                report=self.report,
                action=ModerationLog.Action.DISMISS_REPORT,
            ).exists()
        )

    def test_non_staff_gets_403(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        resp = self.client.patch(self.url, {"status": "reviewed"}, format="json")
        self.assertEqual(resp.status_code, 403)


# ── admin ban/unban ───────────────────────────────────────────────────────────


class AdminBanUnbanTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _admin(username="ban_admin", email="ba@test.com")
        self.target = _buyer(username="ban_target", email="bt@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))

    def test_ban_user(self):
        resp = self.client.post(reverse("admin_ban_user", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)

    def test_ban_user_creates_moderation_log(self):
        self.client.post(reverse("admin_ban_user", args=[self.target.pk]))
        self.assertTrue(
            ModerationLog.objects.filter(
                action=ModerationLog.Action.BAN_USER,
                target_user=self.target,
            ).exists()
        )

    def test_cannot_ban_admin(self):
        resp = self.client.post(reverse("admin_ban_user", args=[self.admin_user.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_unban_user(self):
        self.target.is_active = False
        self.target.save()
        resp = self.client.post(reverse("admin_unban_user", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)

    def test_unban_creates_moderation_log(self):
        self.target.is_active = False
        self.target.save()
        self.client.post(reverse("admin_unban_user", args=[self.target.pk]))
        self.assertTrue(
            ModerationLog.objects.filter(
                action=ModerationLog.Action.UNBAN_USER,
                target_user=self.target,
            ).exists()
        )

    def test_non_staff_gets_403_on_ban(self):
        regular = _buyer(username="reg", email="reg@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.post(reverse("admin_ban_user", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_non_staff_gets_403_on_unban(self):
        regular = _buyer(username="reg2", email="reg2@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.post(reverse("admin_unban_user", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 403)


# ── admin delete/restore project ──────────────────────────────────────────────


class AdminProjectDeleteRestoreTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _admin(username="proj_admin", email="pa@test.com")
        self.seller = _seller(username="proj_seller", email="ps@test.com")
        self.project = _published_project(self.seller, "To Delete")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))
        self.delete_url = reverse("admin_delete_project", args=[self.project.pk])
        self.restore_url = reverse("admin_restore_project", args=[self.project.pk])

    def test_delete_project_soft_deletes(self):
        resp = self.client.post(self.delete_url)
        self.assertEqual(resp.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.REMOVED)

    def test_delete_creates_moderation_log(self):
        self.client.post(self.delete_url)
        self.assertTrue(
            ModerationLog.objects.filter(
                action=ModerationLog.Action.DELETE_PROJECT,
                target_project=self.project,
            ).exists()
        )

    def test_restore_removed_project(self):
        self.project.status = Project.Status.REMOVED
        self.project.save()
        resp = self.client.post(self.restore_url)
        self.assertEqual(resp.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.PUBLISHED)

    def test_restore_creates_moderation_log(self):
        self.project.status = Project.Status.REMOVED
        self.project.save()
        self.client.post(self.restore_url)
        self.assertTrue(
            ModerationLog.objects.filter(
                action=ModerationLog.Action.RESTORE_PROJECT,
                target_project=self.project,
            ).exists()
        )

    def test_restore_non_removed_fails(self):
        resp = self.client.post(self.restore_url)
        self.assertEqual(resp.status_code, 400)

    def test_non_staff_gets_403_on_delete(self):
        regular = _buyer(username="nr", email="nr@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.post(self.delete_url)
        self.assertEqual(resp.status_code, 403)

    def test_non_staff_gets_403_on_restore(self):
        self.project.status = Project.Status.REMOVED
        self.project.save()
        regular = _buyer(username="nr2", email="nr2@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.post(self.restore_url)
        self.assertEqual(resp.status_code, 403)


# ── moderation log ────────────────────────────────────────────────────────────


class ModerationLogTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _admin(username="log_admin", email="la@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.admin_user))
        self.url = reverse("admin_moderation_log")

    def test_log_empty_initially(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 0)

    def test_log_shows_all_actions(self):
        target = _buyer(username="log_target", email="lt@test.com")
        self.client.post(reverse("admin_ban_user", args=[target.pk]))
        self.client.post(reverse("admin_unban_user", args=[target.pk]))
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.json()["results"]), 2)

    def test_non_staff_gets_403(self):
        regular = _buyer(username="nrs", email="nrs@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_log_includes_expected_fields(self):
        target = _buyer(username="log_tgt2", email="lt2@test.com")
        self.client.post(reverse("admin_ban_user", args=[target.pk]))
        log_entry = ModerationLog.objects.first()
        serializer = __import__("moderation.serializers", fromlist=["ModerationLogSerializer"])
        data = serializer.ModerationLogSerializer(log_entry).data
        expected = {
            "id",
            "admin",
            "admin_username",
            "action",
            "target_user",
            "target_user_username",
            "target_project",
            "target_project_title",
            "report",
            "reason",
            "created_at",
        }
        self.assertEqual(set(data.keys()), expected)
