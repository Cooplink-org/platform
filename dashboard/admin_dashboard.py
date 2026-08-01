"""
Unfold admin dashboard callback — platform-wide metrics for the staff index page.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.utils import timezone

from listings.models import Category, Project
from moderation.models import Report
from orders.models import Order, Transaction
from payouts.models import PayoutRequest

User = get_user_model()


def dashboard_callback(_request, context):
    now = timezone.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    paid_orders = Order.objects.filter(status=Order.Status.PAID)

    # ── GMV (Gross Merchandise Value) ────────────────────────────────────
    gmv_all = paid_orders.aggregate(total=Sum("price_at_purchase"))["total"] or 0
    gmv_this_month = (
        paid_orders.filter(paid_at__gte=this_month_start).aggregate(total=Sum("price_at_purchase"))[
            "total"
        ]
        or 0
    )
    gmv_last_month = (
        paid_orders.filter(paid_at__gte=last_month_start, paid_at__lt=this_month_start).aggregate(
            total=Sum("price_at_purchase")
        )["total"]
        or 0
    )
    gmv_7d = (
        paid_orders.filter(paid_at__gte=seven_days_ago).aggregate(total=Sum("price_at_purchase"))[
            "total"
        ]
        or 0
    )

    # ── Platform fee revenue ─────────────────────────────────────────────
    platform_fees = Transaction.objects.filter(type=Transaction.Type.PLATFORM_FEE)
    fees_all = platform_fees.aggregate(total=Sum("amount"))["total"] or 0
    fees_this_month = (
        platform_fees.filter(created_at__gte=this_month_start).aggregate(total=Sum("amount"))[
            "total"
        ]
        or 0
    )

    # ── User stats ───────────────────────────────────────────────────────
    total_users = User.objects.count()
    active_sellers = (
        User.objects.filter(is_seller=True, projects__status=Project.Status.PUBLISHED)
        .distinct()
        .count()
    )
    new_users_7d = User.objects.filter(date_joined__gte=seven_days_ago).count()
    new_users_30d = User.objects.filter(date_joined__gte=thirty_days_ago).count()

    # ── Project stats ────────────────────────────────────────────────────
    published_listings = Project.objects.filter(status=Project.Status.PUBLISHED).count()
    pending_review = Project.objects.filter(status=Project.Status.PENDING_REVIEW).count()
    draft_projects = Project.objects.filter(status=Project.Status.DRAFT).count()
    rejected_projects = Project.objects.filter(status=Project.Status.REJECTED).count()

    # ── Order stats ──────────────────────────────────────────────────────
    orders_this_month = paid_orders.filter(paid_at__gte=this_month_start).count()
    orders_last_month = paid_orders.filter(
        paid_at__gte=last_month_start, paid_at__lt=this_month_start
    ).count()
    orders_7d = paid_orders.filter(paid_at__gte=seven_days_ago).count()
    refunded_count = Order.objects.filter(status=Order.Status.REFUNDED).count()

    # ── Payout stats ─────────────────────────────────────────────────────
    pending_payouts = PayoutRequest.objects.filter(status=PayoutRequest.Status.REQUESTED).count()
    processing_payouts = PayoutRequest.objects.filter(
        status=PayoutRequest.Status.PROCESSING
    ).count()
    total_payouts = (
        PayoutRequest.objects.filter(status=PayoutRequest.Status.COMPLETED).aggregate(
            total=Sum("amount")
        )["total"]
        or 0
    )

    # ── Moderation stats ─────────────────────────────────────────────────
    open_reports = Report.objects.filter(status="open").count()
    reports_7d = Report.objects.filter(created_at__gte=seven_days_ago).count()

    # ── 14-day revenue & orders trend for chart (single grouped query) ───
    from django.db.models.functions import TruncDate

    trend_start = seven_days_ago.date()
    trend_end = (seven_days_ago + timedelta(days=13)).date()

    # Build lookup dict: {date: (revenue, count)}
    daily_lookup = {}
    for row in (
        paid_orders.filter(paid_at__date__gte=trend_start, paid_at__date__lte=trend_end)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(
            day_revenue=Sum("price_at_purchase"),
            day_count=Count("id"),
        )
        .order_by("day")
    ):
        daily_lookup[row["day"]] = (float(row["day_revenue"] or 0), row["day_count"])

    revenue_trend = []
    orders_trend = []
    labels_trend = []
    for i in range(14):
        day = trend_start + timedelta(days=i)
        rev, cnt = daily_lookup.get(day, (0.0, 0))
        revenue_trend.append(rev)
        orders_trend.append(cnt)
        labels_trend.append(day.strftime("%b %d"))

    # ── Projects by category for pie chart ───────────────────────────────
    projects_by_category = list(
        Category.objects.annotate(project_count=Count("projects")).values_list(
            "name", "project_count"
        )
    )
    pie_chart_labels = [str(name) for name, _ in projects_by_category]
    pie_chart_data = [int(count) for _, count in projects_by_category]

    # ── Top 5 best-selling projects by revenue ───────────────────────────
    top_projects = (
        paid_orders.select_related("project")
        .values("project__title")
        .annotate(total_revenue=Sum("price_at_purchase"))
        .order_by("-total_revenue")[:5]
    )
    top_projects_labels = [str(p["project__title"]) for p in top_projects]
    top_projects_data = [float(p["total_revenue"]) for p in top_projects]

    # ── Top sellers by revenue ───────────────────────────────────────────
    top_sellers = (
        paid_orders.values("seller__username")
        .annotate(total_revenue=Sum("seller_earning_amount"), total_sales=Count("id"))
        .order_by("-total_revenue")[:5]
    )
    top_sellers_labels = [str(s["seller__username"]) for s in top_sellers]
    top_sellers_revenue = [float(s["total_revenue"]) for s in top_sellers]
    top_sellers_sales = [int(s["total_sales"]) for s in top_sellers]

    # ── Recent activity (last 10 orders) ─────────────────────────────────
    recent_orders = (
        Order.objects.select_related("buyer", "seller", "project")
        .order_by("-created_at")[:10]
        .values(
            "id",
            "buyer__username",
            "seller__username",
            "project__title",
            "price_at_purchase",
            "status",
            "created_at",
        )
    )

    # ── Pending items needing attention ──────────────────────────────────
    needs_attention = {
        "pending_projects": pending_review,
        "open_reports": open_reports,
        "pending_payouts": pending_payouts,
    }

    context.update(
        {
            # GMV
            "gmv_all": f"{gmv_all:,.0f}",
            "gmv_this_month": f"{gmv_this_month:,.0f}",
            "gmv_last_month": f"{gmv_last_month:,.0f}",
            "gmv_7d": f"{gmv_7d:,.0f}",
            # Fees
            "fees_all": f"{fees_all:,.0f}",
            "fees_this_month": f"{fees_this_month:,.0f}",
            # Users
            "total_users": total_users,
            "active_sellers": active_sellers,
            "new_users_7d": new_users_7d,
            "new_users_30d": new_users_30d,
            # Projects
            "published_listings": published_listings,
            "pending_review": pending_review,
            "draft_projects": draft_projects,
            "rejected_projects": rejected_projects,
            # Orders
            "orders_this_month": orders_this_month,
            "orders_last_month": orders_last_month,
            "orders_7d": orders_7d,
            "refunded_count": refunded_count,
            # Payouts
            "pending_payouts": pending_payouts,
            "processing_payouts": processing_payouts,
            "total_payouts": f"{total_payouts:,.0f}",
            # Moderation
            "open_reports": open_reports,
            "reports_7d": reports_7d,
            # Charts — JSON-encoded to prevent XSS from DB-sourced labels
            "revenue_trend": json.dumps(revenue_trend),
            "labels_trend": json.dumps(labels_trend),
            "orders_trend": json.dumps(orders_trend),
            "pie_chart_labels": json.dumps(pie_chart_labels),
            "pie_chart_data": json.dumps(pie_chart_data),
            "top_projects_labels": json.dumps(top_projects_labels),
            "top_projects_data": json.dumps(top_projects_data),
            "top_sellers_labels": json.dumps(top_sellers_labels),
            "top_sellers_revenue": json.dumps(top_sellers_revenue),
            "top_sellers_sales": json.dumps(top_sellers_sales),
            # Activity
            "recent_orders": list(recent_orders),
            "needs_attention": needs_attention,
        }
    )
    return context
