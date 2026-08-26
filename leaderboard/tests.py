from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from payments.models import PaymentProviderConfig

from .models import LeaderboardEntry, LeaderboardSettings


def _make_paid(brand, amount, paid_at=None, domain=None):
    return LeaderboardEntry.objects.create(
        domain=domain or f"{brand.lower()}.uz",
        brand_name=brand,
        amount_uzs=Decimal(str(amount)),
        status=LeaderboardEntry.Status.PAID,
        paid_at=paid_at or timezone.now(),
    )


class RankingTests(TestCase):
    def test_higher_payment_ranks_higher(self):
        _make_paid("Alpha", 100_000)
        _make_paid("Beta", 500_000)
        _make_paid("Gamma", 300_000)
        ranked = [e.brand_name for e in LeaderboardEntry.ranked()]
        self.assertEqual(ranked, ["Beta", "Gamma", "Alpha"])

    def test_equal_amounts_keep_first_come_first(self):
        early = timezone.now()
        _make_paid("Early", 100_000, paid_at=early)
        _make_paid("Late", 100_000, paid_at=timezone.now())
        ranked = [e.brand_name for e in LeaderboardEntry.ranked()]
        self.assertEqual(ranked, ["Early", "Late"])

    def test_prospective_position(self):
        _make_paid("A", 500_000)
        _make_paid("B", 300_000)
        _make_paid("C", 300_000)
        # Above everyone
        self.assertEqual(LeaderboardEntry.prospective_position(Decimal("600000")), 1)
        # Between 500k and 300k
        self.assertEqual(LeaderboardEntry.prospective_position(Decimal("400000")), 2)
        # Equal to the two 300k entries → sits below both
        self.assertEqual(LeaderboardEntry.prospective_position(Decimal("300000")), 4)
        # Below everyone
        self.assertEqual(LeaderboardEntry.prospective_position(Decimal("1")), 4)

    def test_pending_entries_do_not_count(self):
        LeaderboardEntry.objects.create(
            domain="pending.uz", brand_name="Pending", amount_uzs=Decimal("999999")
        )
        _make_paid("Paid", 100_000)
        self.assertEqual(LeaderboardEntry.prospective_position(Decimal("500000")), 1)
        self.assertEqual(LeaderboardEntry.total_earned(), Decimal("100000.00"))

    def test_total_earned(self):
        _make_paid("A", 100_000)
        _make_paid("B", 250_000.50)
        self.assertEqual(LeaderboardEntry.total_earned(), Decimal("350000.50"))


class LeaderboardAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.settings_cfg = LeaderboardSettings.load()

    def test_public_leaderboard_endpoint(self):
        _make_paid("Alpha", 100_000)
        _make_paid("Beta", 900_000)
        resp = self.client.get(reverse("leaderboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 2)
        self.assertEqual(resp.json()["entries"][0]["brand_name"], "Beta")
        self.assertEqual(resp.json()["entries"][0]["position"], 1)
        self.assertEqual(resp.json()["total_earned_uzs"], "1000000.00")

    def test_entry_create_validates_fields(self):
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={"domain": "no-dot", "brand_name": "", "amount_uzs": "abc"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        errors = resp.json()["errors"]
        self.assertIn("domain", errors)
        self.assertIn("brand_name", errors)
        self.assertIn("amount_uzs", errors)

    def test_entry_create_enforces_min_amount(self):
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={
                "domain": "acme.uz",
                "brand_name": "Acme",
                "amount_uzs": "5000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("amount_uzs", resp.json()["errors"])

    def test_entry_create_returns_position(self):
        _make_paid("Top", 1_000_000)
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={
                "domain": "acme.uz",
                "brand_name": "Acme",
                "description": "We build things",
                "logo_url": "https://acme.uz/logo.png",
                "amount_uzs": "1500000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["position"], 1)
        entry = LeaderboardEntry.objects.get(pk=body["entry"]["id"])
        self.assertEqual(entry.status, LeaderboardEntry.Status.PENDING_PAYMENT)

    def test_entry_create_rejected_when_disabled(self):
        self.settings_cfg.enabled = False
        self.settings_cfg.save()
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={"domain": "a.uz", "brand_name": "A", "amount_uzs": "50000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    @patch("leaderboard.views.InPayClient")
    def test_entry_pay_returns_redirect(self, mock_client_cls):
        PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            merchant_id="M1",
            merchant_token_encrypted="",
        )
        mock_client_cls.return_value.create_payment.return_value = (
            "INPAY-1",
            "https://pay.inpay.uz/checkout/INPAY-1",
            {"success": True},
        )
        entry = LeaderboardEntry.objects.create(
            domain="acme.uz", brand_name="Acme", amount_uzs=Decimal("150000")
        )
        resp = self.client.post(reverse("leaderboard_entry_pay", args=[entry.id]))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["payid"], "INPAY-1")
        self.assertTrue(body["redirect_url"].startswith("https://pay.inpay.uz/"))
        entry.refresh_from_db()
        self.assertEqual(entry.payment_ref, "INPAY-1")
        # The payment description and amount overrides reach the gateway
        _args, kwargs = mock_client_cls.return_value.create_payment.call_args
        self.assertEqual(kwargs["amount"], Decimal("150000"))
        self.assertEqual(kwargs["description"], "Crack It #1")

    def test_entry_pay_404_when_already_paid(self):
        entry = _make_paid("Done", 100_000)
        resp = self.client.post(reverse("leaderboard_entry_pay", args=[entry.id]))
        self.assertEqual(resp.status_code, 404)


class LeaderboardWebhookTests(TestCase):
    def _post_webhook(self, order_id):
        import json

        return self.client.post(
            reverse("inpay_webhook"),
            data=json.dumps({"order_id": order_id, "status": "success", "amount": 100000}),
            content_type="application/json",
        )

    @patch("payments.views.InPayClient")
    def test_webhook_confirms_leaderboard_entry(self, mock_client_cls):
        mock_client_cls.return_value.check_status.return_value = {
            "status": "success",
            "amount": 100000,
        }
        entry = LeaderboardEntry.objects.create(
            domain="acme.uz",
            brand_name="Acme",
            amount_uzs=Decimal("100000"),
            payment_ref="INPAY-42",
        )
        resp = self._post_webhook("INPAY-42")
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.status, LeaderboardEntry.Status.PAID)
        self.assertIsNotNone(entry.paid_at)

    @patch("payments.views.InPayClient")
    def test_webhook_idempotent_for_entry(self, mock_client_cls):
        mock_client_cls.return_value.check_status.return_value = {"status": "success"}
        entry = _make_paid("Paid", 100_000)
        entry.payment_ref = "INPAY-43"
        entry.save(update_fields=["payment_ref"])
        resp = self._post_webhook("INPAY-43")
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.status, LeaderboardEntry.Status.PAID)

    @patch("payments.views.InPayClient")
    def test_webhook_keeps_entry_pending_on_failed_verification(self, mock_client_cls):
        mock_client_cls.return_value.check_status.return_value = {"status": "failed"}
        entry = LeaderboardEntry.objects.create(
            domain="acme.uz",
            brand_name="Acme",
            amount_uzs=Decimal("100000"),
            payment_ref="INPAY-44",
        )
        resp = self._post_webhook("INPAY-44")
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.status, LeaderboardEntry.Status.FAILED)


class LeaderboardVerifyTests(TestCase):
    @patch("leaderboard.views.InPayClient")
    def test_verify_confirms_paid_entry(self, mock_client_cls):
        mock_client_cls.return_value.check_status.return_value = {
            "status": "success",
            "amount": 100000,
        }
        _make_paid("Top", 900_000)
        entry = LeaderboardEntry.objects.create(
            domain="acme.uz",
            brand_name="Acme",
            amount_uzs=Decimal("100000"),
            payment_ref="INPAY-77",
        )
        resp = self.client.post(
            reverse("leaderboard_verify"), data={"order_id": "INPAY-77"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "paid")
        self.assertEqual(body["entry"]["position"], 2)
        entry.refresh_from_db()
        self.assertEqual(entry.status, LeaderboardEntry.Status.PAID)

    def test_verify_unknown_ref(self):
        resp = self.client.post(
            reverse("leaderboard_verify"), data={"order_id": "nope"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "unknown")

    @patch("leaderboard.views.InPayClient")
    def test_verify_by_entry_id(self, mock_client_cls):
        """The /crack-it return URL carries the entry id — verify must resolve it."""
        mock_client_cls.return_value.check_status.return_value = {
            "status": "success",
            "amount": 100000,
        }
        entry = LeaderboardEntry.objects.create(
            domain="acme.uz",
            brand_name="Acme",
            amount_uzs=Decimal("100000"),
            payment_ref="INPAY-99",
        )
        resp = self.client.post(
            reverse("leaderboard_verify"), data={"entry_id": entry.id}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "paid")
        entry.refresh_from_db()
        self.assertEqual(entry.status, LeaderboardEntry.Status.PAID)


class EntryMetricsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_payload_exposes_category_likes_clicks(self):
        entry = _make_paid("Alpha", 100_000)
        LeaderboardEntry.objects.filter(pk=entry.pk).update(likes=12, clicks=34)
        resp = self.client.get(reverse("leaderboard"))
        payload = resp.json()["entries"][0]
        self.assertEqual(payload["category"], LeaderboardEntry.Category.TECH)
        self.assertEqual(payload["likes"], 12)
        self.assertEqual(payload["clicks"], 34)

    def test_create_entry_defaults_to_tech_category(self):
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={"domain": "acme.uz", "brand_name": "Acme", "amount_uzs": "50000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["entry"]["category"], LeaderboardEntry.Category.TECH)

    def test_create_entry_accepts_category(self):
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={
                "domain": "acme.uz",
                "brand_name": "Acme",
                "amount_uzs": "50000",
                "category": LeaderboardEntry.Category.AI,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["entry"]["category"], LeaderboardEntry.Category.AI)

    def test_create_entry_rejects_unknown_category(self):
        resp = self.client.post(
            reverse("leaderboard_entry_create"),
            data={
                "domain": "acme.uz",
                "brand_name": "Acme",
                "amount_uzs": "50000",
                "category": "spaceships",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("category", resp.json()["errors"])

    def test_click_endpoint_increments_counter(self):
        entry = _make_paid("Alpha", 100_000)
        r1 = self.client.post(reverse("leaderboard_entry_click", args=[entry.id]))
        r2 = self.client.post(reverse("leaderboard_entry_click", args=[entry.id]))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["clicks"], 1)
        self.assertEqual(r2.json()["clicks"], 2)

    def test_click_endpoint_unknown_entry_returns_404(self):
        resp = self.client.post(reverse("leaderboard_entry_click", args=[9999]))
        self.assertEqual(resp.status_code, 404)
