from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import WebhookLog


@admin.register(WebhookLog)
class WebhookLogAdmin(ModelAdmin):
    list_display = ("endpoint", "received_at", "matched_order")
    readonly_fields = ("endpoint", "raw_body", "verification_response", "received_at", "matched_order")
    search_fields = ("endpoint",)
    ordering = ("-received_at",)
