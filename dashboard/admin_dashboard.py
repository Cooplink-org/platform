"""
Unfold admin dashboard callback — platform-wide metrics for the staff index page.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.utils import timezone

from listings.models import Category, Project
from orders.models import Order

User = get_user_model()


def dashboard_callback(request, context):
    """
    Injected via UNFOLD["DASHBOARD_CALLBACK"] — populates the admin index
    template with key platform metrics.
    """
    now = timezone.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)

    paid_orders = Order.objects.filter(status=Order.Status.PAID)

    # Gross Merchandise Value — total of price_at_purchase for all paid orders
    gmv_all = paid_orders.aggregate(
        total=Sum("price_at_purchase")
    )["total"] or 0

    gmv_this_month = paid_orders.filter(
        paid_at__gte=this_month_start
    ).aggregate(total=Sum("price_at_purchase"))["total"] or 0

    gmv_last_month = paid_orders.filter(
        paid_at__gte=last_month_start,
        paid_at__lt=this_month_start,
    ).aggregate(total=Sum("price_at_purchase"))["total"] or 0

    # Active sellers — anyone with is_seller=True and at least one published project
    active_sellers = User.objects.filter(
        is_seller=True,
        projects__status=Project.Status.PUBLISHED,
    ).distinct().count()

    # Total published listings
    published_listings = Project.objects.filter(
        status=Project.Status.PUBLISHED
    ).count()

    # Orders this month vs last month
    orders_this_month = paid_orders.filter(
        paid_at__gte=this_month_start
    ).count()
    orders_last_month = paid_orders.filter(
        paid_at__gte=last_month_start,
        paid_at__lt=this_month_start,
    ).count()

    # 7-day revenue trend for chart
    seven_days_ago = now - timedelta(days=7)
    revenue_trend = []
    labels_trend = []
    for i in range(7):
        day = seven_days_ago + timedelta(days=i+1)
        day_total = paid_orders.filter(
            paid_at__date=day.date()
        ).aggregate(total=Sum("price_at_purchase"))["total"] or 0
        revenue_trend.append(float(day_total))
        labels_trend.append(day.strftime("%b %d"))

    # Projects by category for pie chart
    projects_by_category = list(
        Category.objects.annotate(
            project_count=Count("projects")
        ).values_list("name", "project_count")
    )
    pie_chart_labels = [str(name) for name, _ in projects_by_category]
    pie_chart_data = [int(count) for _, count in projects_by_category]

    # Top 5 best-selling projects by revenue
    top_projects = (
        paid_orders.select_related("project")
        .values("project__title")
        .annotate(total_revenue=Sum("price_at_purchase"))
        .order_by("-total_revenue")[:5]
    )
    top_projects_labels = [str(p["project__title"]) for p in top_projects]
    top_projects_data = [float(p["total_revenue"]) for p in top_projects]

    context.update({
        "gmv_all": f"{gmv_all:,.2f}",
        "gmv_this_month": f"{gmv_this_month:,.2f}",
        "gmv_last_month": f"{gmv_last_month:,.2f}",
        "active_sellers": active_sellers,
        "published_listings": published_listings,
        "orders_this_month": orders_this_month,
        "orders_last_month": orders_last_month,
        "revenue_trend": revenue_trend,
        "labels_trend": labels_trend,
        "pie_chart_labels": pie_chart_labels,
        "pie_chart_data": pie_chart_data,
        "top_projects_labels": top_projects_labels,
        "top_projects_data": top_projects_data,
    })
    return context
