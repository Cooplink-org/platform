from django.conf import settings
from django.db import models


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
    destination_card_encrypted = models.TextField(
        help_text="Fernet-encrypted card number. Decrypted only on the admin detail page."
    )
    destination_card_last4 = models.CharField(max_length=4)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.REQUESTED
    )
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
        return f"Payout {self.id} — {self.seller.username} — {self.amount} ({self.get_status_display()})"
