from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from listings.models import Category, Project
from orders.models import Order, Transaction

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


def _published_project(seller, title="Proj", **kw):
    cat, _ = Category.objects.get_or_create(name="Web", defaults={"slug": "web"})
    defaults = dict(
        title=title,
        slug=title.lower().replace(" ", "-"),
        description="d",
        price="50000.00",
        status=Project.Status.PUBLISHED,
        seller=seller,
        category=cat,
    )
    defaults.update(kw)
    return Project.objects.create(**defaults)


def _paid_order(buyer, project, seller, price="50000.00", fee="5000.00", earning="45000.00"):
    order = Order.objects.create(
        buyer=buyer,
        project=project,
        seller=seller,
        price_at_purchase=price,
        platform_fee_percent=Decimal("10.00"),
        platform_fee_amount=fee,
        seller_earning_amount=earning,
        status=Order.Status.PAID,
    )
    return order


def _earning_tx(user, order, amount="45000.00", days_ago=None):
    tx = Transaction.objects.create(
        user=user,
        order=order,
        type=Transaction.Type.SALE_EARNING,
        amount=amount,
    )
    if days_ago is not None:
        past = timezone.now() - timedelta(days=days_ago)
        Transaction.objects.filter(pk=tx.pk).update(created_at=past)
        tx.refresh_from_db()
    return tx


# ── dashboard summary ─────────────────────────────────────────────────────────


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class DashboardSummaryTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="d_seller", email="ds@test.com")
        self.buyer = _buyer(username="d_buyer", email="db@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.url = reverse("dashboard-summary")

    def test_summary_returns_zero_when_no_data(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lifetime_revenue"], 0)
        self.assertEqual(data["available_balance"], 0)
        self.assertEqual(data["pending_balance"], 0)
        self.assertEqual(data["total_sales"], 0)
        self.assertEqual(data["total_published_listings"], 0)
        self.assertEqual(data["total_downloads"], 0)
        self.assertIsNone(data["next_unlock_date"])

    def test_summary_counts_sales_and_revenue(self):
        project = _published_project(self.seller, "Revenue Test")
        order = _paid_order(self.buyer, project, self.seller, earning="45000.00")
        _earning_tx(self.seller, order, "45000.00", days_ago=10)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(data["lifetime_revenue"], Decimal("45000.00"))
        self.assertEqual(data["total_sales"], 1)
        self.assertEqual(data["available_balance"], Decimal("45000.00"))

    def test_summary_counts_published_listings(self):
        _published_project(self.seller, "Listing 1")
        _published_project(self.seller, "Listing 2")
        resp = self.client.get(self.url)
        self.assertEqual(resp.json()["total_published_listings"], 2)

    def test_summary_pending_balance_for_recent_earnings(self):
        project = _published_project(self.seller, "Pending Earn")
        order = _paid_order(self.buyer, project, self.seller, earning="30000.00")
        _earning_tx(self.seller, order, "30000.00", days_ago=1)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(data["pending_balance"], Decimal("30000.00"))
        self.assertEqual(data["available_balance"], 0)
        self.assertIsNotNone(data["next_unlock_date"])

    def test_summary_requires_auth(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_summary_has_expected_fields(self):
        resp = self.client.get(self.url)
        expected = {
            "lifetime_revenue",
            "available_balance",
            "pending_balance",
            "next_unlock_date",
            "total_sales",
            "total_published_listings",
            "total_downloads",
        }
        self.assertEqual(set(resp.json().keys()), expected)


# ── dashboard sales ───────────────────────────────────────────────────────────


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class DashboardSalesListTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="ds_seller", email="dss@test.com")
        self.buyer = _buyer(username="ds_buyer", email="dsb@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.url = reverse("dashboard-sales")

    def test_empty_when_no_sales(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 0)

    def test_lists_seller_orders(self):
        project = _published_project(self.seller, "Sales Proj")
        _paid_order(self.buyer, project, self.seller)
        resp = self.client.get(self.url)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["buyer_username"], "ds_buyer")
        self.assertEqual(results[0]["project_title"], "Sales Proj")

    def test_does_not_include_other_seller_orders(self):
        other = _seller(username="other_seller", email="os@test.com")
        other_project = _published_project(other, "Other")
        _paid_order(self.buyer, other_project, other)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.json()["results"]), 0)

    def test_sales_list_requires_auth(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)


# ── dashboard listings ────────────────────────────────────────────────────────


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class DashboardListingsListTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="dl_seller", email="dls@test.com")
        self.buyer = _buyer(username="dl_buyer", email="dlb@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.url = reverse("dashboard-listings")

    def test_empty_when_no_listings(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 0)

    def test_lists_own_projects(self):
        _published_project(self.seller, "My Listing")
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.json()["results"]), 1)

    def test_annotates_sales_count_and_revenue(self):
        project = _published_project(self.seller, "Annotated")
        _paid_order(self.buyer, project, self.seller, earning="45000.00")
        resp = self.client.get(self.url)
        listing = resp.json()["results"][0]
        self.assertEqual(listing["sales_count"], 1)
        self.assertEqual(listing["revenue"], "45000.00")

    def test_does_not_include_other_seller_listings(self):
        other = _seller(username="another", email="an@test.com")
        _published_project(other, "Other's")
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.json()["results"]), 0)

    def test_listings_requires_auth(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)


# ── earnings timeseries ───────────────────────────────────────────────────────


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class DashboardEarningsTimeseriesTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="et_seller", email="ets@test.com")
        self.buyer = _buyer(username="et_buyer", email="etb@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.url = reverse("dashboard-earnings-timeseries")

    def _make_earning(self, amount="10000.00", days_ago=0):
        project = _published_project(self.seller, f"Earn Proj {amount}")
        order = _paid_order(self.buyer, project, self.seller, earning=amount)
        return _earning_tx(self.seller, order, amount, days_ago=days_ago)

    def test_empty_when_no_earnings(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_single_earning_returned(self):
        self._make_earning("50000.00", days_ago=1)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["earnings"], "50000.00")
        self.assertIn("date", data[0])

    def test_multiple_earnings_on_same_day_aggregated(self):
        project = _published_project(self.seller, "Multi Sale")
        order1 = _paid_order(self.buyer, project, self.seller, earning="10000.00")
        order2 = _paid_order(self.buyer, project, self.seller, earning="20000.00")
        _earning_tx(self.seller, order1, "10000.00", days_ago=1)
        _earning_tx(self.seller, order2, "20000.00", days_ago=1)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["earnings"], "30000.00")

    def test_earnings_across_multiple_days(self):
        self._make_earning("10000.00", days_ago=2)
        self._make_earning("20000.00", days_ago=1)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(len(data), 2)

    def test_earnings_range_param(self):
        self._make_earning("10000.00", days_ago=40)
        resp = self.client.get(f"{self.url}?range=30d")
        self.assertEqual(len(resp.json()), 0)

        resp = self.client.get(f"{self.url}?range=60d")
        self.assertEqual(len(resp.json()), 1)

    def test_earnings_require_auth(self):
        resp = APIClient().get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_earnings_returns_correct_data_contract(self):
        self._make_earning("150000.00", days_ago=5)
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertIn("date", entry)
        self.assertIn("earnings", entry)
        self.assertEqual(len(entry.keys()), 2)
