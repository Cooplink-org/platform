from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from listings.models import Project
from orders.models import Order, Transaction

from .models import WebhookLog

User = get_user_model()

APIView.throttle_classes = []
SimpleRateThrottle.THROTTLE_RATES = {"anon": None, "user": None, "burst": None}


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


class WebhookLogModelTest(TestCase):
    def test_webhook_log_creation(self):
        wh = WebhookLog.objects.create(
            endpoint="success",
            raw_body="payid=123&summa=100000",
            verification_response={"status": "success"},
        )
        self.assertEqual(wh.endpoint, "success")
        self.assertEqual(wh.raw_body, "payid=123&summa=100000")
        self.assertEqual(wh.verification_response, {"status": "success"})
        self.assertIsNotNone(wh.received_at)
        self.assertIsNone(wh.matched_order)
        self.assertIn("success", str(wh))


@override_settings(
    MIRPAY_KASSA_ID="test_kassa",
    MIRPAY_API_KEY="test_key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class MirPayWebhookTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(username="wh_buyer", password="pass")
        self.seller = User.objects.create_user(
            username="wh_seller", password="pass", is_seller=True
        )
        self.project = Project.objects.create(
            title="Test Project",
            slug="test-project",
            description="Test",
            price=Decimal("100000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("100000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("10000.00"),
            seller_earning_amount=Decimal("90000.00"),
            status=Order.Status.PENDING_PAYMENT,
        )

    def _form_body(self, overrides=None):
        data = {
            "payid": "MP789",
            "summa": "100000",
            "status": "success",
            "comment": f"Buyurtma ID: {self.order.id}",
            "chek": "check123",
            "fiskal": "fiskal123",
            "sana": "2025-01-01",
        }
        if overrides:
            data.update(overrides)
        return urlencode(data)

    @patch("payments.views.mirpay.check_status")
    def test_success_webhook_marks_order_paid(self, mock_check_status):
        mock_check_status.return_value = {"status": "success", "summa": "100000", "payid": "MP789"}
        resp = self.client.post(
            reverse("mirpay_webhook_success"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertIsNotNone(self.order.paid_at)
        self.assertTrue(
            Transaction.objects.filter(
                order=self.order, type=Transaction.Type.SALE_EARNING
            ).exists()
        )
        self.assertTrue(
            Transaction.objects.filter(
                order=self.order, type=Transaction.Type.PLATFORM_FEE
            ).exists()
        )
        self.assertTrue(
            WebhookLog.objects.filter(matched_order=self.order, endpoint="success").exists()
        )

    @patch("payments.views.mirpay.check_status")
    def test_success_webhook_idempotent(self, mock_check_status):
        mock_check_status.return_value = {"status": "success", "summa": "100000", "payid": "MP789"}
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])
        resp = self.client.post(
            reverse("mirpay_webhook_success"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    @patch("payments.views.mirpay.check_status")
    def test_success_webhook_verification_mismatch_keeps_pending(self, mock_check_status):
        mock_check_status.return_value = {"status": "failed", "summa": "100000", "payid": "MP789"}
        resp = self.client.post(
            reverse("mirpay_webhook_success"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_success_webhook_missing_payid(self):
        resp = self.client.post(
            reverse("mirpay_webhook_success"),
            data=self._form_body({"payid": ""}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    @patch("payments.views.mirpay.check_status")
    def test_fail_webhook_marks_order_failed(self, mock_check_status):
        mock_check_status.return_value = {"status": "failed", "summa": "100000", "payid": "MP789"}
        resp = self.client.post(
            reverse("mirpay_webhook_fail"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "failed")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    @patch("payments.views.mirpay.check_status")
    def test_fail_webhook_creates_webhook_log(self, mock_check_status):
        mock_check_status.return_value = {"status": "failed", "summa": "100000", "payid": "MP789"}
        self.client.post(
            reverse("mirpay_webhook_fail"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertTrue(WebhookLog.objects.filter(endpoint="fail").exists())

    @patch("payments.views.mirpay.get_balance")
    def test_balance_staff_only(self, mock_get_balance):
        mock_get_balance.return_value = {"balance": "5000000"}
        resp = self.client.get(reverse("mirpay_balance"))
        self.assertEqual(resp.status_code, 401)
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("mirpay_balance"))
        self.assertEqual(resp.status_code, 403)
        staff = User.objects.create_superuser(username="wh_admin", password="pass")
        self.client.force_authenticate(staff)
        resp = self.client.get(reverse("mirpay_balance"))
        self.assertEqual(resp.status_code, 200)

    @patch("payments.views.mirpay.check_status")
    def test_success_webhook_creates_both_transactions(self, mock_check_status):
        mock_check_status.return_value = {"status": "success", "summa": "100000", "payid": "MP789"}
        self.client.post(
            reverse("mirpay_webhook_success"),
            data=self._form_body(),
            content_type="application/x-www-form-urlencoded",
        )
        earning_tx = Transaction.objects.filter(
            order=self.order, type=Transaction.Type.SALE_EARNING
        ).first()
        fee_tx = Transaction.objects.filter(
            order=self.order, type=Transaction.Type.PLATFORM_FEE
        ).first()
        self.assertIsNotNone(earning_tx)
        self.assertIsNotNone(fee_tx)
        self.assertEqual(earning_tx.amount, Decimal("90000.00"))
        self.assertEqual(fee_tx.amount, Decimal("10000.00"))
