from django.contrib import admin
from django.utils import timezone
from unfold.admin import ModelAdmin

from .models import LeaderboardEntry, LeaderboardSettings


@admin.register(LeaderboardSettings)
class LeaderboardSettingsAdmin(ModelAdmin):
    list_display = ("enabled", "min_amount_uzs", "updated_at")

    fieldsets = (
        (
            "Crack It leaderboard",
            {
                "fields": ("enabled", "min_amount_uzs"),
                "description": (
                    "Master settings for the /crack-it page. Rank is derived from "
                    "the paid amount (higher payment = higher place), so there is "
                    "no manual ordering to maintain."
                ),
            },
        ),
    )

    def has_add_permission(self, _request):
        # Singleton — only one settings row ever exists.
        return not LeaderboardSettings.objects.exists()

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(LeaderboardEntry)
class LeaderboardEntryAdmin(ModelAdmin):
    list_display = (
        "id",
        "brand_name",
        "domain",
        "amount_uzs",
        "status",
        "category",
        "likes",
        "clicks",
        "rank",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "category")
    search_fields = ("brand_name", "domain", "payment_ref")
    list_display_links = ("id", "brand_name")
    ordering = ("-amount_uzs", "paid_at", "id")
    readonly_fields = ("created_at", "paid_at", "payment_ref", "rank")
    actions = ("mark_paid", "mark_failed")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "domain",
                    "brand_name",
                    "description",
                    "logo_url",
                    "amount_uzs",
                    "status",
                    "category",
                    "rank",
                ),
            },
        ),
        (
            "Engagement",
            {
                "fields": ("likes", "clicks"),
                "description": (
                    "Metrics shown on the public leaderboard. Clicks increment "
                    "automatically via the public click endpoint; likes are "
                    "curated here."
                ),
            },
        ),
        (
            "Payment",
            {"fields": ("payment_ref", "created_at", "paid_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Rank")
    def rank(self, obj):
        if obj.status != LeaderboardEntry.Status.PAID:
            return "—"
        ranked = {e.id: i + 1 for i, e in enumerate(LeaderboardEntry.ranked())}
        return ranked.get(obj.id)

    @admin.action(description="Mark selected entries as paid")
    def mark_paid(self, request, queryset):
        for entry in queryset.filter(status=LeaderboardEntry.Status.PENDING_PAYMENT):
            entry.status = LeaderboardEntry.Status.PAID
            entry.paid_at = entry.paid_at or timezone.now()
            entry.save(update_fields=["status", "paid_at"])
        self.message_user(request, f"{queryset.count()} entries marked as paid.")

    @admin.action(description="Mark selected entries as failed")
    def mark_failed(self, request, queryset):
        queryset.filter(status=LeaderboardEntry.Status.PENDING_PAYMENT).update(
            status=LeaderboardEntry.Status.FAILED
        )
        self.message_user(request, "Selected pending entries marked as failed.")
