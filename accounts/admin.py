from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Sum
from unfold.admin import ModelAdmin

from orders.models import Order, Transaction

from .models import User


class TransactionInline(admin.TabularInline):
    """Read-only inline showing user's ledger entries."""

    model = Transaction
    fields = ("type", "amount", "order", "created_at")
    readonly_fields = ("type", "amount", "order", "created_at")
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("-created_at",)

    def has_add_permission(self, _request, _obj=None):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    list_display = (
        "username",
        "email",
        "is_seller",
        "is_active",
        "is_staff",
        "_last_ip",
        "date_joined",
    )
    list_filter = ("is_seller", "is_active", "is_staff")
    search_fields = ("username", "email", "github_username", "last_login_ip")
    inlines = [TransactionInline]

    # Expose custom fields in the admin detail view
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "GitHub Profile",
            {
                "fields": (
                    "github_id",
                    "github_username",
                    "avatar_url",
                    "bio",
                    "is_seller",
                ),
            },
        ),
        (
            "IP Tracking",
            {
                "fields": ("last_login_ip", "signup_ip"),
                "description": (
                    "IP addresses captured from user requests. "
                    "Useful for fraud detection and location tracking."
                ),
            },
        ),
        (
            "Seller stats (read-only)",
            {
                "fields": ("_lifetime_sales_count", "_lifetime_revenue"),
                "description": (
                    "Computed from paid orders. These values are not stored "
                    "— they are calculated on the fly whenever you open this page."
                ),
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "GitHub Profile",
            {
                "fields": (
                    "github_id",
                    "github_username",
                    "avatar_url",
                    "bio",
                    "is_seller",
                ),
            },
        ),
    )
    readonly_fields = ("_lifetime_sales_count", "_lifetime_revenue")

    @admin.display(description="Last IP")
    def _last_ip(self, obj):
        return obj.last_login_ip or "—"

    # ── computed fields ──────────────────────────────────────────────────

    @admin.display(description="Lifetime sales count")
    def _lifetime_sales_count(self, obj):
        if obj and obj.pk:
            return Order.objects.filter(seller=obj, status=Order.Status.PAID).count()
        return 0

    @admin.display(description="Lifetime revenue (UZS)")
    def _lifetime_revenue(self, obj):
        if obj and obj.pk:
            total = Order.objects.filter(seller=obj, status=Order.Status.PAID).aggregate(
                total=Sum("seller_earning_amount")
            )["total"]
            return f"{total or 0:,.2f}"
        return "0.00"
