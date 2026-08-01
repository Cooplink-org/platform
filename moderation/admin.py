from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import AICodeReview, ModerationLog, Report


@admin.register(Report)
class ReportAdmin(ModelAdmin):
    list_display = ("id", "reporter", "reason", "status", "created_at")
    list_filter = ("reason", "status")
    search_fields = ("reporter__username", "detail")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ["reporter", "project", "reported_user"]


@admin.register(ModerationLog)
class ModerationLogAdmin(ModelAdmin):
    list_display = ("action", "admin", "created_at")
    list_filter = ("action",)
    readonly_fields = ("created_at",)
    autocomplete_fields = ["admin", "target_user", "target_project", "report"]


@admin.register(AICodeReview)
class AICodeReviewAdmin(ModelAdmin):
    list_display = (
        "project",
        "_status_badge",
        "_malware_badge",
        "match_percentage",
        "model_used",
        "tokens_used",
        "reviewed_at",
    )
    list_filter = ("status", "is_malware", "model_used")
    search_fields = ("project__title", "summary", "model_used")
    readonly_fields = (
        "project",
        "status",
        "is_malware",
        "malware_score",
        "match_percentage",
        "summary",
        "malware_findings",
        "description_analysis",
        "model_used",
        "tokens_used",
        "raw_response",
        "reviewed_at",
        "reviewed_by",
    )
    autocomplete_fields = ["project", "reviewed_by"]

    @admin.display(description="Review Status")
    def _status_badge(self, obj):
        colors = {
            AICodeReview.Status.PASSED: ("#16a34a", "✓ PASSED"),
            AICodeReview.Status.FLAGGED_MALWARE: ("#dc2626", "⚠ MALWARE DETECTED"),
            AICodeReview.Status.DESCRIPTION_MISMATCH: ("#d97706", "⚡ DESCRIPTION MISMATCH"),
            AICodeReview.Status.ERROR: ("#6b7280", "✗ ERROR"),
        }
        color, label = colors.get(obj.status, ("#6b7280", obj.get_status_display()))
        return format_html(
            '<span style="background-color: {}; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">{}</span>',  # noqa: E501
            color,
            label,
        )

    @admin.display(description="Malware Assessment")
    def _malware_badge(self, obj):
        if obj.is_malware or obj.malware_score > 40:
            return format_html(
                '<span style="color: #dc2626; font-weight: bold;">⚠ Risk: {}%</span>',
                obj.malware_score,
            )
        return format_html(
            '<span style="color: #16a34a; font-weight: bold;">✓ Clean ({}%)</span>',
            obj.malware_score,
        )
