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

from .inpay import InPayError
from .models import PaymentProviderConfig, WebhookLog

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


# ── PaymentProviderConfig model ──────────────────────────────────────────────


# Valid Fernet key: 32 url-safe base64-encoded bytes (44 chars total)
VALID_FERNET_KEY = "tNRYfzfLIs70GgPOWiVo7DNEmos3RflMhk4OZ0pROTQ="


@override_settings(
    FERNET_KEY=VALID_FERNET_KEY,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class PaymentProviderConfigTest(TestCase):
    def test_create_config(self):
        config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            merchant_id="1353",
            merchant_token_encrypted="encrypted_value",
        )
        self.assertEqual(str(config), "inPAY (enabled)")
        self.assertTrue(config.enabled)

    def test_disabled_config_str(self):
        config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=False,
        )
        self.assertEqual(str(config), "inPAY (disabled)")

    def test_only_one_default_at_a_time(self):
        c1 = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            is_default=True,
        )
        c2 = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.MIRPAY,
            enabled=True,
            is_default=True,
        )
        c1.refresh_from_db()
        self.assertFalse(c1.is_default)
        self.assertTrue(c2.is_default)

    def test_disabled_cannot_be_default(self):
        config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=False,
            is_default=True,
        )
        config.refresh_from_db()
        self.assertFalse(config.is_default)

    def test_merchant_token_property_decrypts(self):
        from accounts.utils import encrypt_token

        plaintext = "6a7bf375b302cfcda6692e6f60402cb3"
        config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            merchant_token_encrypted=encrypt_token(plaintext),
        )
        self.assertEqual(config.merchant_token, plaintext)

    def test_merchant_token_empty_when_not_set(self):
        config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
        )
        self.assertEqual(config.merchant_token, "")


# ── inPAY webhook ────────────────────────────────────────────────────────────


@override_settings(
    FERNET_KEY=VALID_FERNET_KEY,
    MIRPAY_KASSA_ID="test_kassa",
    MIRPAY_API_KEY="test_key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class InPayWebhookTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(username="inpay_buyer", password="pass")
        self.seller = User.objects.create_user(
            username="inpay_seller", password="pass", is_seller=True
        )
        self.project = Project.objects.create(
            title="inPAY Test",
            slug="inpay-test",
            description="Test",
            price=Decimal("15000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("15000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("1500.00"),
            seller_earning_amount=Decimal("13500.00"),
            status=Order.Status.PENDING_PAYMENT,
            payment_ref="1ff2f5a6d66f6e9c",
            provider=Order.Provider.INPAY,
        )
        self.inpay_config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            merchant_id="1353",
            merchant_token_encrypted="encrypted",
        )

    def _webhook_body(self, overrides=None):
        data = {
            "amount": "15000.00",
            "status": "success",
            "order_id": "1ff2f5a6d66f6e9c",
            "transaction_id": 149,
            "created_at": "2025-12-10 05:14:52",
        }
        if overrides:
            data.update(overrides)
        return data

    @patch("payments.views.InPayClient")
    def test_success_webhook_marks_order_paid(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "success",
            "amount": 15000,
            "payment_method": "click",
        }
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body(),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"OK")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertIsNotNone(self.order.paid_at)
        self.assertTrue(
            Transaction.objects.filter(
                order=self.order, type=Transaction.Type.SALE_EARNING
            ).exists()
        )
        self.assertTrue(
            WebhookLog.objects.filter(
                matched_order=self.order, endpoint="inpay"
            ).exists()
        )

    @patch("payments.views.InPayClient")
    def test_webhook_idempotent_on_paid_order(self, mock_inpay_client):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body(),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # InPayClient should not have been called (order already processed)
        mock_inpay_client.assert_not_called()

    @patch("payments.views.InPayClient")
    def test_webhook_verification_pending_keeps_order_pending(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "pending",
            "amount": 15000,
        }
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body(),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    @patch("payments.views.InPayClient")
    def test_webhook_failed_status_marks_order_failed(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "failed",
            "amount": 15000,
        }
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body({"status": "failed"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    def test_webhook_missing_order_id(self):
        resp = self.client.post(
            reverse("inpay_webhook"),
            data={"amount": "15000", "status": "success"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_no_matching_order(self):
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body({"order_id": "nonexistent_id"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_invalid_json(self):
        resp = self.client.post(
            reverse("inpay_webhook"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    @patch("payments.views.InPayClient")
    def test_webhook_verification_failure_returns_502(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.side_effect = Exception("Connection refused")
        resp = self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body(),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 502)

    @patch("payments.views.InPayClient")
    def test_webhook_creates_both_transactions(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "success",
            "amount": 15000,
        }
        self.client.post(
            reverse("inpay_webhook"),
            data=self._webhook_body(),
            content_type="application/json",
        )
        earning_tx = Transaction.objects.filter(
            order=self.order, type=Transaction.Type.SALE_EARNING
        ).first()
        fee_tx = Transaction.objects.filter(
            order=self.order, type=Transaction.Type.PLATFORM_FEE
        ).first()
        self.assertIsNotNone(earning_tx)
        self.assertIsNotNone(fee_tx)
        self.assertEqual(earning_tx.amount, Decimal("13500.00"))
        self.assertEqual(fee_tx.amount, Decimal("1500.00"))


# ── inPAY verify endpoint ────────────────────────────────────────────────────


@override_settings(
    FERNET_KEY=VALID_FERNET_KEY,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class InPayVerifyTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(username="inpay_v_buyer", password="pass")
        self.seller = User.objects.create_user(
            username="inpay_v_seller", password="pass", is_seller=True
        )
        self.project = Project.objects.create(
            title="inPAY Verify",
            slug="inpay-verify",
            description="Test",
            price=Decimal("15000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("15000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("1500.00"),
            seller_earning_amount=Decimal("13500.00"),
            status=Order.Status.PENDING_PAYMENT,
            payment_ref="1ff2f5a6d66f6e9c",
            provider=Order.Provider.INPAY,
        )
        self.inpay_config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            merchant_id="1353",
            merchant_token_encrypted="encrypted",
        )
        self.client.force_authenticate(self.buyer)

    @patch("payments.views.InPayClient")
    def test_verify_marks_paid(self, mock_inpay_client):
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "success",
            "amount": 15000,
        }
        resp = self.client.post(
            reverse("inpay_verify_payment"),
            data={"order_id": "1ff2f5a6d66f6e9c"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "paid")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_verify_missing_order_id(self):
        resp = self.client.post(
            reverse("inpay_verify_payment"),
            data={},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("payments.views.InPayClient")
    def test_verify_inpay_not_configured(self, mock_inpay_client):
        mock_inpay_client.side_effect = InPayError("inPAY is not configured or disabled")
        self.inpay_config.enabled = False
        self.inpay_config.save()
        resp = self.client.post(
            reverse("inpay_verify_payment"),
            data={"order_id": "1ff2f5a6d66f6e9c"},
            format="json",
        )
        self.assertEqual(resp.status_code, 503)

    @patch("payments.views.InPayClient")
    def test_verify_already_paid(self, mock_inpay_client):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])
        mock_client = mock_inpay_client.return_value
        mock_client.check_status.return_value = {
            "success": True,
            "order_id": "1ff2f5a6d66f6e9c",
            "status": "success",
            "amount": 15000,
        }
        resp = self.client.post(
            reverse("inpay_verify_payment"),
            data={"order_id": "1ff2f5a6d66f6e9c"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "paid")
