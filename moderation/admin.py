from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ModerationLog, Report


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
