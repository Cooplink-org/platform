import io
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework.test import APIClient

from listings.models import Category, Project, ProjectSnapshot
from .models import Order, Transaction

User = get_user_model()


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
        self.assertEqual(order.seller, self.seller)
        self.assertEqual(order.price_at_purchase, Decimal("100000.00"))
        self.assertEqual(order.platform_fee_amount, Decimal("10000.00"))
        self.assertEqual(order.seller_earning_amount, Decimal("90000.00"))
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertIsNotNone(order.created_at)
        self.assertIsNone(order.paid_at)
        self.assertIsNone(order.payment_ref)

    def test_order_str(self):
        order = self._create_order()
        expected = f"Order {order.id} — Test Project by buyer"
        self.assertEqual(str(order), expected)

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

    def test_order_status_choices_count(self):
        self.assertEqual(len(Order.Status.choices), 4)


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
        self.assertEqual(tx.order, self.order)
        self.assertEqual(tx.amount, Decimal("900.00"))
        self.assertEqual(tx.type, Transaction.Type.SALE_EARNING)
        self.assertIsNotNone(tx.created_at)

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
