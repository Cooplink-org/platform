from django.db import models


class PaymentProviderConfig(models.Model):
    """Admin-configurable payment provider settings (stored in DB).

    Lets staff enable/disable a provider, set credentials, and configure
    callback/return URLs — all from the Django admin without touching .env.
    """

    class Provider(models.TextChoices):
        INPAY = "inpay", "inPAY"
        MIRPAY = "mirpay", "MirPay"

    provider = models.CharField(max_length=20, choices=Provider.choices, unique=True)
    enabled = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)

    # inPAY credentials
    merchant_id = models.CharField(max_length=50, blank=True)
    merchant_token_encrypted = models.TextField(blank=True)

    # URLs
    callback_url = models.URLField(blank=True)
    return_url = models.URLField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider"]

    def __str__(self):
        state = "enabled" if self.enabled else "disabled"
        return f"{self.get_provider_display()} ({state})"

    @property
    def merchant_token(self):
        """Decrypt and return the merchant token (empty string if not set)."""
        if not self.merchant_token_encrypted:
            return ""
        from accounts.utils import decrypt_token

        return decrypt_token(self.merchant_token_encrypted)

    def save(self, *args, **kwargs):
        # Ensure only one default provider at a time
        if self.is_default and self.enabled:
            PaymentProviderConfig.objects.filter(is_default=True).exclude(
                pk=self.pk
            ).update(is_default=False)
        elif not self.enabled:
            self.is_default = False
        super().save(*args, **kwargs)


class WebhookLog(models.Model):
    endpoint = models.CharField(max_length=100)
    raw_body = models.TextField()
    verification_response = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    matched_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.endpoint} @ {self.received_at.isoformat()}"
