from django.contrib import admin

from .models import Notification, PhoneVerificationCode, TelegramLinkingToken


@admin.register(TelegramLinkingToken)
class TelegramLinkingTokenAdmin(admin.ModelAdmin):
    list_display = ("token", "user", "created_at", "expires_at", "consumed")
    list_filter = ("consumed",)
    search_fields = ("user__username", "user__email", "token")
    readonly_fields = ("token", "created_at")
    raw_id_fields = ("user",)


@admin.register(PhoneVerificationCode)
class PhoneVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "created_at", "expires_at", "used", "attempts")
    list_filter = ("used",)
    search_fields = ("user__username", "user__email", "phone_number")
    readonly_fields = ("code", "created_at")
    raw_id_fields = ("user",)

    def has_change_permission(self, _request, _obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "type", "title", "is_read", "created_at")
    list_filter = ("type", "is_read", "created_at")
    search_fields = ("recipient__username", "recipient__email", "title")
    readonly_fields = ("recipient", "type", "title", "body", "link", "is_read", "created_at")
    raw_id_fields = ("recipient",)

    def has_add_permission(self, _request):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False
