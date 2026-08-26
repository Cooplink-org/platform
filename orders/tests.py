import io
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from listings.models import Category, Project, ProjectSnapshot
from payments.models import PaymentProviderConfig

from .models import Order, Transaction

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


def _category():
    cat, _ = Category.objects.get_or_create(name="Sales", defaults={"slug": "sales"})
    return cat


# ── order model ───────────────────────────────────────────────────────────────


class OrderModelTest(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer", password="pass")
        self.seller = User.objects.create_user(username="seller", password="pass", is_seller=True)
        self.project = Project.objects.create(
            title="Test Project",
            slug="test-project",
            description="A test project",
            price=Decimal("100000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
        )

    def _create_order(self, status=Order.Status.PENDING_PAYMENT):
        return Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("100000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("10000.00"),
            seller_earning_amount=Decimal("90000.00"),
            status=status,
        )

    def test_order_creation(self):
        order = self._create_order()
        self.assertEqual(order.buyer, self.buyer)
        self.assertEqual(order.project, self.project)
        self.assertEqual(order.price_at_purchase, Decimal("100000.00"))
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_order_str(self):
        order = self._create_order()
        self.assertIn(str(order.id), str(order))
        self.assertIn("Test Project", str(order))

    def test_order_default_fee_percent(self):
        order = Order(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("50000.00"),
            platform_fee_amount=Decimal("5000.00"),
            seller_earning_amount=Decimal("45000.00"),
        )
        self.assertEqual(order.platform_fee_percent, Decimal("10.00"))

    def test_order_status_choices(self):
        self.assertEqual(len(Order.Status.choices), 4)


# ── order create ──────────────────────────────────────────────────────────────


@override_settings(
    MIRPAY_KASSA_ID="test_kassa",
    MIRPAY_API_KEY="test_key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class OrderCreateTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = _buyer(username="ord_buyer", email="ob@test.com")
        self.seller = _seller(username="ord_seller", email="os@test.com")
        self.project = Project.objects.create(
            title="For Sale",
            slug="for-sale",
            description="d",
            price=Decimal("50000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
            category=_category(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.buyer))
        self.url = reverse("order_create")
        PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.MIRPAY,
            enabled=True,
            is_default=True,
            merchant_id="1430",
            merchant_token_encrypted="encrypted",
        )

    @patch("payments.mirpay.MirPayClient.create_payment")
    @patch("payments.mirpay.MirPayClient.get_token")
    def test_create_order_success(self, mock_get_token, mock_create_payment):
        mock_get_token.return_value = "tok123"
        mock_create_payment.return_value = (
            "MP001",
            "https://mirpay.uz/pay/123",
            {"payid": "MP001"},
        )
        resp = self.client.post(self.url, {"project_id": self.project.id}, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("id", data)
        self.assertEqual(data["status"], "pending_payment")
        self.assertEqual(data["price"], "50000.00")
        self.assertEqual(data["redirect_url"], "https://mirpay.uz/pay/123")

    def test_create_order_missing_project_id(self):
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_create_order_requires_auth(self):
        resp = APIClient().post(self.url, {"project_id": self.project.id}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_create_order_404_for_draft_project(self):
        draft = Project.objects.create(
            title="Draft",
            slug="draft",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.post(self.url, {"project_id": draft.id}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_create_order_404_for_nonexistent_project(self):
        resp = self.client.post(self.url, {"project_id": 99999}, format="json")
        self.assertEqual(resp.status_code, 404)

    @patch("payments.mirpay.MirPayClient.create_payment")
    @patch("payments.mirpay.MirPayClient.get_token")
    def test_create_order_502_on_mirpay_failure(self, mock_get_token, mock_create_payment):
        mock_get_token.return_value = "tok123"
        mock_create_payment.side_effect = Exception("Connection refused")
        resp = self.client.post(self.url, {"project_id": self.project.id}, format="json")
        self.assertEqual(resp.status_code, 502)


# ── order download ────────────────────────────────────────────────────────────


class OrderDownloadTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(username="buyer", password="pass")
        self.seller = User.objects.create_user(username="seller", password="pass", is_seller=True)
        self.other = User.objects.create_user(username="other", password="pass")
        self.project = Project.objects.create(
            title="Test Project",
            slug="test-project",
            description="A test project",
            price=Decimal("100000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
        )

    def _create_order(self, status=Order.Status.PAID, payment_ref="MP123"):
        return Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase=Decimal("100000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("10000.00"),
            seller_earning_amount=Decimal("90000.00"),
            status=status,
            payment_ref=payment_ref,
            paid_at=timezone.now() if status == Order.Status.PAID else None,
        )

    def test_download_requires_auth(self):
        order = self._create_order()
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 401)

    def test_download_only_buyer(self):
        order = self._create_order()
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 403)

    def test_download_requires_paid_status(self):
        order = self._create_order(status=Order.Status.PENDING_PAYMENT)
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Only paid orders", resp.json()["detail"])

    def test_download_404_no_snapshot(self):
        order = self._create_order()
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 404)

    @patch("django.core.files.storage.FileSystemStorage.open")
    def test_download_returns_file(self, mock_storage_open):
        mock_storage_open.return_value = io.BytesIO(b"fake archive content")
        order = self._create_order()
        ProjectSnapshot.objects.create(
            project=self.project,
            version=1,
            archive="snapshots/test/snapshot.zip",
        )
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Content-Disposition", resp)

    def test_download_403_for_wrong_user(self):
        order = self._create_order()
        self.client.force_authenticate(self.seller)
        resp = self.client.get(reverse("order_download", args=[order.id]))
        self.assertEqual(resp.status_code, 403)


# ── transaction model ─────────────────────────────────────────────────────────


class TransactionModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.other = User.objects.create_user(username="seller", password="pass", is_seller=True)
        self.project = Project.objects.create(
            title="Test",
            slug="test",
            description="Test",
            price="1000.00",
            status=Project.Status.PUBLISHED,
            seller=self.other,
        )
        self.order = Order.objects.create(
            buyer=self.user,
            project=self.project,
            seller=self.other,
            price_at_purchase=Decimal("1000.00"),
            platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("100.00"),
            seller_earning_amount=Decimal("900.00"),
        )

    def test_transaction_creation(self):
        tx = Transaction.objects.create(
            user=self.user,
            order=self.order,
            type=Transaction.Type.SALE_EARNING,
            amount=Decimal("900.00"),
        )
        self.assertEqual(tx.user, self.user)
        self.assertEqual(tx.amount, Decimal("900.00"))

    def test_transaction_str(self):
        tx = Transaction.objects.create(
            user=self.user,
            order=self.order,
            type=Transaction.Type.PLATFORM_FEE,
            amount=Decimal("100.00"),
        )
        self.assertIn("Platform Fee", str(tx))

    def test_transaction_type_choices(self):
        self.assertEqual(len(Transaction.Type.choices), 4)


# ── order create with inPAY ─────────────────────────────────────────────────


# Valid Fernet key: 32 url-safe base64-encoded bytes (44 chars total)
VALID_FERNET_KEY = "tNRYfzfLIs70GgPOWiVo7DNEmos3RflMhk4OZ0pROTQ="


@override_settings(
    FERNET_KEY=VALID_FERNET_KEY,
    MIRPAY_KASSA_ID="test_kassa",
    MIRPAY_API_KEY="test_key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class OrderCreateInPayTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = _buyer(username="inpay_ord_buyer", email="iob@test.com")
        self.seller = _seller(username="inpay_ord_seller", email="ios@test.com")
        self.project = Project.objects.create(
            title="inPAY Sale",
            slug="inpay-sale",
            description="d",
            price=Decimal("50000.00"),
            status=Project.Status.PUBLISHED,
            seller=self.seller,
            category=_category(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.buyer))
        self.url = reverse("order_create")
        self.inpay_config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.INPAY,
            enabled=True,
            is_default=True,
            merchant_id="1353",
            merchant_token_encrypted="encrypted",
        )
        self.mirpay_config = PaymentProviderConfig.objects.create(
            provider=PaymentProviderConfig.Provider.MIRPAY,
            enabled=True,
            is_default=False,
            merchant_id="1430",
            merchant_token_encrypted="encrypted",
        )

    @patch("orders.views._create_inpay_payment")
    def test_create_order_inpay_success(self, mock_create_inpay):
        mock_create_inpay.return_value = (
            "1ff2f5a6d66f6e9c",
            "https://inpay.uz/checkout/1ff2f5a6d66f6e9c",
            {"success": True, "order_id": "1ff2f5a6d66f6e9c"},
        )
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id, "payment_provider": "inpay"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["provider"], "inpay")
        self.assertEqual(data["redirect_url"], "https://inpay.uz/checkout/1ff2f5a6d66f6e9c")
        order = Order.objects.filter(buyer=self.buyer).latest("id")
        self.assertEqual(order.provider, "inpay")
        self.assertEqual(order.payment_ref, "1ff2f5a6d66f6e9c")

    @patch("orders.views._create_inpay_payment")
    def test_create_order_inpay_uses_default_provider(self, mock_create_inpay):
        """When no payment_provider is specified, uses the default from admin config."""
        mock_create_inpay.return_value = (
            "abc123",
            "https://inpay.uz/checkout/abc123",
            {"success": True, "order_id": "abc123"},
        )
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["provider"], "inpay")
        mock_create_inpay.assert_called_once()

    def test_create_order_inpay_disabled_returns_503(self):
        self.inpay_config.enabled = False
        self.inpay_config.save()
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id, "payment_provider": "inpay"},
            format="json",
        )
        self.assertEqual(resp.status_code, 503)

    def test_create_order_unknown_provider_returns_400(self):
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id, "payment_provider": "unknown"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("orders.views._create_mirpay_payment")
    def test_create_order_mirpay_still_works(self, mock_create_mirpay):
        """Explicitly requesting MirPay still works."""
        mock_create_mirpay.return_value = (
            "MP001",
            "https://mirpay.uz/pay/123",
            {"payid": "MP001"},
        )
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id, "payment_provider": "mirpay"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["provider"], "mirpay")

    @patch("orders.views._create_mirpay_payment")
    @patch("orders.views._create_inpay_payment")
    def test_create_order_inpay_failure_no_fallback(self, mock_create_inpay, mock_create_mirpay):
        from payments.inpay import InPayError

        mock_create_inpay.side_effect = InPayError("inPAY Strict mode IP rejection")
        resp = self.client.post(
            self.url,
            {"project_id": self.project.id, "payment_provider": "inpay"},
            format="json",
        )
        self.assertEqual(resp.status_code, 503)
        mock_create_mirpay.assert_not_called()

    def test_create_order_inpay_failure_returns_503(self):
        from payments.inpay import InPayError

        with patch("orders.views._create_inpay_payment", side_effect=InPayError("inPAY down")):
            resp = self.client.post(
                self.url,
                {"project_id": self.project.id, "payment_provider": "inpay"},
                format="json",
            )
        self.assertEqual(resp.status_code, 503)
