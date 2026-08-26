from decimal import Decimal

from django.conf import settings
from django.db import models


class PayoutFeeConfig(models.Model):
    """Singleton — admin-configurable withdrawal fee charged to sellers.

    A percentage of each payout request amount is deducted as a platform fee.
    The fee is snapshotted onto each PayoutRequest so historical records stay
    accurate even if the admin later changes the percentage.
    """

    fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("3.00"),
        help_text="Percentage deducted from each seller payout request.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Withdrawal fee"
        verbose_name_plural = "Withdrawal fees"

    def __str__(self):
        return f"Withdrawal fee: {self.fee_percent}%"

    def save(self, *args, **kwargs):
        # Enforce a singleton row.
        self.pk = self._default_singleton_pk()
        super().save(*args, **kwargs)

    @classmethod
    def _default_singleton_pk(cls):
        return cls.objects.first().pk if cls.objects.exists() else 1

    @classmethod
    def get_fee_percent(cls) -> Decimal:
        config, _ = cls.objects.get_or_create(pk=1)
        return config.fee_percent


class PayoutRequest(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payout_requests",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payout_fee_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    payout_fee_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    destination_card_encrypted = models.TextField(
        help_text="Fernet-encrypted card number. Decrypted only on the admin detail page."
    )
    destination_card_last4 = models.CharField(max_length=4)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    admin_note = models.TextField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_payouts",
    )

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return (
            f"Payout {self.id} — {self.seller.username} — "
            f"{self.amount} ({self.get_status_display()})"
        )
