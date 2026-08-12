from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin

from accounts.utils import decrypt_token, encrypt_token

from .models import PaymentProviderConfig, WebhookLog


class PaymentProviderConfigForm(forms.ModelForm):
    """Custom form that handles merchant token encryption transparently."""

    merchant_token = forms.CharField(
        widget=forms.PasswordInput(
            render_value=True, attrs={"placeholder": "32-character merchant token"}
        ),
        required=False,
        help_text="Merchant token from inPAY dashboard. Stored encrypted in the database. "
        "Leave blank to keep the existing token.",
    )

    class Meta:
        model = PaymentProviderConfig
        fields = [
            "provider",
            "enabled",
            "is_default",
            "merchant_id",
            "callback_url",
            "return_url",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["provider"].disabled = True

    def save(self, commit=True):
        instance = super().save(commit=False)
        token = self.cleaned_data.get("merchant_token")
        if token:
            instance.merchant_token_encrypted = encrypt_token(token)
        # If no new token entered, keep the existing encrypted value
        if commit:
            instance.save()
        return instance


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(ModelAdmin):
    form = PaymentProviderConfigForm
    list_display = ("provider", "enabled", "is_default", "merchant_id", "updated_at")
    list_filter = ("enabled", "is_default", "provider")
    readonly_fields = ("created_at", "updated_at", "_decrypted_token_display")

    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "enabled", "is_default"),
            },
        ),
        (
            "inPAY Credentials",
            {
                "fields": ("merchant_id", "merchant_token", "_decrypted_token_display"),
                "description": (
                    "Configure inPAY credentials from your merchant dashboard at "
                    "inpay.uz. The merchant token is stored encrypted (Fernet) and "
                    "only decrypted when the payment client needs it."
                ),
            },
        ),
        (
            "URLs",
            {
                "fields": ("callback_url", "return_url"),
                "description": (
                    "callback_url — the URL inPAY sends webhook notifications to. "
                    "return_url — the page customers are redirected to after payment."
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        fs = super().get_fieldsets(request, obj)
        if obj is None:
            # Hide the decrypted token display when creating a new config
            return [
                (n, f) for n, f in fs if "_decrypted_token_display" not in f.get("fields", [])
            ]
        return fs

    @admin.display(description="Current token (masked)")
    def _decrypted_token_display(self, obj):
        if obj and obj.merchant_token_encrypted:
            try:
                token = decrypt_token(obj.merchant_token_encrypted)
                if len(token) > 8:
                    return f"{token[:4]}••••••••{token[-4:]}"
                return "••••••••"
            except Exception:
                return "*** decryption error ***"
        return ""


@admin.register(WebhookLog)
class WebhookLogAdmin(ModelAdmin):
    list_display = ("endpoint", "received_at", "matched_order")
    readonly_fields = (
        "endpoint",
        "raw_body",
        "verification_response",
        "received_at",
        "matched_order",
    )
    search_fields = ("endpoint",)
    ordering = ("-received_at",)
