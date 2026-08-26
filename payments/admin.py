from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin

from accounts.utils import decrypt_token, encrypt_token

from .models import PaymentProviderConfig, WebhookLog


class PaymentProviderConfigForm(forms.ModelForm):
    """Custom form that handles merchant token encryption transparently."""

    merchant_token = forms.CharField(
        widget=forms.PasswordInput(
            render_value=True, attrs={"placeholder": "Provider secret token / API key"}
        ),
        required=False,
        help_text=(
            "inPAY: merchant token from inpay.uz dashboard. "
            "MirPay: API key from mirpay.uz dashboard. Stored encrypted in the database. "
            "Leave blank to keep the existing value."
        ),
    )

    callback_secret = forms.CharField(
        widget=forms.PasswordInput(
            render_value=True, attrs={"placeholder": "Webhook HMAC secret (MirPay)"}
        ),
        required=False,
        help_text=(
            "MirPay only: shared secret used to verify X-MirPay-Signature HMAC "
            "signatures on webhooks. Stored encrypted in the database. "
            "Leave blank to keep the existing value (falls back to the "
            "MIRPAY_CALLBACK_SECRET env var)."
        ),
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
        if self.instance and self.instance.provider == PaymentProviderConfig.Provider.MIRPAY:
            self.fields[
                "merchant_id"
            ].help_text = "MirPay kassa ID (kassaid) from mirpay.uz dashboard."
            self.fields["callback_url"].help_text = "MirPay payment success URL."
        else:
            self.fields["merchant_id"].help_text = "inPAY merchant ID from inpay.uz dashboard."
            self.fields["callback_url"].help_text = "inPAY webhook callback URL."
        if self.instance and self.instance.provider == PaymentProviderConfig.Provider.MIRPAY:
            self.fields[
                "return_url"
            ].help_text = "Page customers are redirected to after MirPay payment."

    def save(self, commit=True):
        instance = super().save(commit=False)
        token = self.cleaned_data.get("merchant_token")
        if token:
            instance.merchant_token_encrypted = encrypt_token(token)
        # If no new token entered, keep the existing encrypted value
        secret = self.cleaned_data.get("callback_secret")
        if secret:
            instance.callback_secret_encrypted = encrypt_token(secret)
        if commit:
            instance.save()
        return instance


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(ModelAdmin):
    form = PaymentProviderConfigForm
    list_display = (
        "provider",
        "enabled",
        "is_default",
        "merchant_id",
        "has_callback_secret",
        "updated_at",
    )
    list_filter = ("enabled", "is_default", "provider")
    readonly_fields = (
        "created_at",
        "updated_at",
        "_decrypted_token_display",
        "_callback_secret_display",
    )

    @admin.display(boolean=True, description="Callback secret")
    def has_callback_secret(self, obj):
        return bool(obj and obj.callback_secret_encrypted)

    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "enabled", "is_default"),
            },
        ),
        (
            "Provider Credentials",
            {
                "fields": (
                    "merchant_id",
                    "merchant_token",
                    "_decrypted_token_display",
                    "callback_secret",
                    "_callback_secret_display",
                ),
                "description": (
                    "inPAY: enter your merchant_id and merchant token from inpay.uz. "
                    "MirPay: enter your kassa ID and API key from mirpay.uz. "
                    "Tokens are stored encrypted (Fernet) and only decrypted when needed."
                ),
            },
        ),
        (
            "URLs",
            {
                "fields": ("callback_url", "return_url"),
                "description": (
                    "callback_url — the URL the provider sends webhook notifications to. "
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
            # Hide the decrypted secret displays when creating a new config
            return [
                (n, f)
                for n, f in fs
                if "_decrypted_token_display" not in f.get("fields", [])
                and "_callback_secret_display" not in f.get("fields", [])
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

    @admin.display(description="Callback secret (masked)")
    def _callback_secret_display(self, obj):
        if obj and obj.callback_secret_encrypted:
            try:
                secret = decrypt_token(obj.callback_secret_encrypted)
                if len(secret) > 8:
                    return f"{secret[:4]}••••••••{secret[-4:]}"
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
