from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from rest_framework.test import APIClient

from orders.models import Order, Transaction
from orders.balance import available_balance, pending_balance
from listings.models import Project
from .models import PayoutRequest

User = get_user_model()


class SellerBalanceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seller", password="pass", is_seller=True)
        self.buyer = User.objects.create_user(username="buyer", password="pass")
        self.project = Project.objects.create(
            title="Test", slug="test", description="Test",
            price=Decimal("100000.00"), status=Project.Status.PUBLISHED, seller=self.user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, project=self.project, seller=self.user,
            price_at_purchase=Decimal("100000.00"), platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("10000.00"), seller_earning_amount=Decimal("90000.00"),
            status=Order.Status.PAID,
        )

    def _create_earning(self, amount=Decimal("90000.00"), days_ago=0):
        tx = Transaction.objects.create(
            user=self.user, order=self.order,
            type=Transaction.Type.SALE_EARNING, amount=amount,
        )
        if days_ago > 0:
            past = timezone.now() - timedelta(days=days_ago)
            Transaction.objects.filter(pk=tx.pk).update(created_at=past)
            tx.refresh_from_db()
        return tx

    def _create_payout(self, amount=Decimal("50000.00")):
        return Transaction.objects.create(
            user=self.user, type=Transaction.Type.PAYOUT, amount=amount,
            order=None,
        )

    def test_available_balance_zero_with_no_earnings(self):
        self.assertEqual(available_balance(self.user), 0)

    def test_available_balance_counts_old_earnings(self):
        self._create_earning(days_ago=10)
        self.assertEqual(available_balance(self.user), Decimal("90000.00"))

    def test_available_balance_excludes_recent_earnings(self):
        self._create_earning(days_ago=1)
        self.assertEqual(available_balance(self.user), 0)

    def test_available_balance_subtracts_payouts(self):
        self._create_earning(days_ago=10)
        self._create_payout(Decimal("30000.00"))
        self.assertEqual(available_balance(self.user), Decimal("60000.00"))

    def test_pending_balance_empty_with_no_earnings(self):
        self.assertEqual(pending_balance(self.user), [])

    def test_pending_balance_includes_recent_earnings(self):
        tx = self._create_earning(days_ago=1)
        items = pending_balance(self.user)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["amount"], Decimal("90000.00"))
        self.assertIn("unlocks_at", items[0])
        self.assertIn("created_at", items[0])

    def test_pending_balance_excludes_old_earnings(self):
        self._create_earning(days_ago=10)
        self.assertEqual(pending_balance(self.user), [])


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class PayoutRequestAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(username="seller", password="pass", is_seller=True)
        self.buyer = User.objects.create_user(username="buyer", password="pass")
        self.project = Project.objects.create(
            title="Test", slug="test", description="Test",
            price=Decimal("100000.00"), status=Project.Status.PUBLISHED, seller=self.seller,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, project=self.project, seller=self.seller,
            price_at_purchase=Decimal("100000.00"), platform_fee_percent=Decimal("10.00"),
            platform_fee_amount=Decimal("10000.00"), seller_earning_amount=Decimal("90000.00"),
            status=Order.Status.PAID,
        )
        # Give seller some available balance (earning older than 7 days)
        tx = Transaction.objects.create(
            user=self.seller, order=self.order,
            type=Transaction.Type.SALE_EARNING, amount=Decimal("90000.00"),
        )
        past = timezone.now() - timedelta(days=10)
        Transaction.objects.filter(pk=tx.pk).update(created_at=past)

    def test_request_payout_requires_auth(self):
        resp = self.client.post(reverse("payout_request_create"), {"amount": "50000", "card_number": "8600123412345678"})
        self.assertEqual(resp.status_code, 401)

    def test_request_payout_success(self):
        self.client.force_authenticate(self.seller)
        resp = self.client.post(reverse("payout_request_create"), {
            "amount": "50000", "card_number": "8600123412345678",
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["amount"], "50000.00")
        self.assertEqual(data["destination_card_last4"], "5678")
        self.assertEqual(data["status"], "requested")
        self.assertNotIn("card_number", data)

    def test_request_payout_exceeds_balance(self):
        self.client.force_authenticate(self.seller)
        resp = self.client.post(reverse("payout_request_create"), {
            "amount": "999999", "card_number": "8600123412345678",
        })
        self.assertEqual(resp.status_code, 400)

    def test_request_payout_invalid_card(self):
        self.client.force_authenticate(self.seller)
        resp = self.client.post(reverse("payout_request_create"), {
            "amount": "50000", "card_number": "123",
        })
        self.assertEqual(resp.status_code, 400)

    def test_mine_returns_payouts_and_balance(self):
        self.client.force_authenticate(self.seller)
        PayoutRequest.objects.create(
            seller=self.seller, amount=Decimal("30000.00"),
            destination_card_encrypted="encrypted", destination_card_last4="5678",
        )
        resp = self.client.get(reverse("payout_list_mine"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("available_balance", data)
        self.assertIn("pending_balance", data)
        self.assertIn("payouts", data)
        self.assertEqual(len(data["payouts"]), 1)
        self.assertEqual(data["payouts"][0]["destination_card_last4"], "5678")
        self.assertNotIn("destination_card_encrypted", data["payouts"][0])
