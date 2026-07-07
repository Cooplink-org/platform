from datetime import timedelta

from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from orders.balance import available_balance, pending_balance
from orders.models import Order, Transaction
from listings.models import Project

from .serializers import (
    DashboardOrderSerializer,
    DashboardProjectSerializer,
    EarningsEntrySerializer,
)


class DashboardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


# ── GET /api/dashboard/summary/ ──────────────────────────────────────────────

@api_view(["GET"])
def dashboard_summary(request):
    user = request.user

    revenue = (
        Order.objects.filter(seller=user, status=Order.Status.PAID)
        .aggregate(total=Sum("seller_earning_amount"))["total"]
        or 0
    )

    avail = available_balance(user)
    pending = pending_balance(user)
    total_pending = sum(item["amount"] for item in pending)
    next_unlock = pending[0]["unlocks_at"] if pending else None

    sales_count = Order.objects.filter(seller=user, status=Order.Status.PAID).count()

    published_count = Project.objects.filter(seller=user, status=Project.Status.PUBLISHED).count()

    total_downloads = (
        Project.objects.filter(seller=user, status=Project.Status.PUBLISHED)
        .aggregate(total=Sum("download_count"))["total"]
        or 0
    )

    return Response({
        "lifetime_revenue": revenue,
        "available_balance": avail,
        "pending_balance": total_pending,
        "next_unlock_date": next_unlock,
        "total_sales": sales_count,
        "total_published_listings": published_count,
        "total_downloads": total_downloads,
    })


# ── GET /api/dashboard/sales/ ────────────────────────────────────────────────

class DashboardSalesList(ListAPIView):
    serializer_class = DashboardOrderSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = DashboardPagination

    def get_queryset(self):
        return Order.objects.filter(seller=self.request.user).select_related(
            "buyer", "project"
        )


# ── GET /api/dashboard/listings/ ─────────────────────────────────────────────

class DashboardListingsList(ListAPIView):
    serializer_class = DashboardProjectSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = DashboardPagination

    def get_queryset(self):
        return (
            Project.objects.filter(seller=self.request.user)
            .annotate(
                sales_count=Count(
                    "orders", filter=Q(orders__status=Order.Status.PAID)
                ),
                revenue=Sum(
                    "orders__seller_earning_amount",
                    filter=Q(orders__status=Order.Status.PAID),
                ),
            )
        )


# ── GET /api/dashboard/earnings-timeseries/ ──────────────────────────────────

@api_view(["GET"])
def dashboard_earnings_timeseries(request):
    raw = request.query_params.get("range", "30d")
    days = 30
    if raw.endswith("d"):
        try:
            days = int(raw[:-1])
        except (ValueError, TypeError):
            pass
    days = max(1, min(days, 365))

    cutoff = timezone.now() - timedelta(days=days)

    qs = (
        Transaction.objects.filter(
            user=request.user,
            type=Transaction.Type.SALE_EARNING,
            created_at__gte=cutoff,
        )
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(earnings=Sum("amount"))
        .order_by("date")
    )

    serializer = EarningsEntrySerializer(qs, many=True)
    return Response(serializer.data)

