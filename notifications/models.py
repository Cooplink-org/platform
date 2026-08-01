import uuid

from django.conf import settings
from django.db import models


class TelegramLinkingToken(models.Model):
    """
    Single-use token that links a Telegram chat to a Cooplink account.
    Generated when the user clicks "Verify phone via Telegram" on settings.
    Consumed when the bot's /start handler processes the deep link payload.
    Expires after 10 minutes.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_linking_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    telegram_chat_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Populated when the token is consumed by the bot.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at", "consumed"]),
        ]

    def __str__(self):
        status = "consumed" if self.consumed else "active"
        return f"LinkingToken({self.token}) [{status}] for user {self.user_id}"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.consumed and not self.is_expired


class PhoneVerificationCode(models.Model):
    """
    Single-use 6-digit verification code sent via Telegram.
    Tied to a Cooplink user and the phone number from the Telegram contact.
    Expires after 5 minutes. Max 5 wrong attempts before invalidation.
    """

    MAX_ATTEMPTS = 5
    CODE_LENGTH = 6

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_verification_codes",
    )
    code = models.CharField(max_length=6)
    phone_number = models.CharField(
        max_length=20,
        help_text="Phone number from Telegram contact (verified as belonging to sender).",
    )
    telegram_chat_id = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(
        default=0,
        help_text="Number of failed verification attempts.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["expires_at", "used"]),
        ]

    def __str__(self):
        status = "used" if self.used else ("expired" if self.is_expired else "active")
        return f"PhoneCode(***{self.code[-4:]}) [{status}] for user {self.user_id}"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.used and not self.is_expired and self.attempts < self.MAX_ATTEMPTS

    def record_attempt(self) -> bool:
        """
        Increment attempt counter. Returns True if still within limits,
        False if max attempts exceeded (code is now invalidated).
        """
        self.attempts += 1
        if self.attempts >= self.MAX_ATTEMPTS:
            self.used = True
        self.save(update_fields=["attempts", "used"])
        return self.attempts < self.MAX_ATTEMPTS


# ── In-platform notification inbox ───────────────────────────────────────────


class Notification(models.Model):
    """
    Persistent, database-backed notification for the in-platform inbox.

    Created automatically by signal handlers in notifications/signals.py
    whenever a relevant event occurs (new question, answered question,
    new review, sale, listing status change, report outcome, etc.).
    """

    class Type(models.TextChoices):
        QA_ASKED = "qa_asked", "Question asked on your listing"
        QA_ANSWERED = "qa_answered", "Your question was answered"
        REVIEW_RECEIVED = "review_received", "New review on your listing"
        REPORT_ACTIONED = "report_actioned", "Your report was actioned"
        REPORT_DISMISSED = "report_dismissed", "Your report was dismissed"
        LISTING_APPROVED = "listing_approved", "Your listing was approved"
        LISTING_REJECTED = "listing_rejected", "Your listing was rejected"
        SALE_MADE = "sale_made", "You made a sale"
        ORDER_PLACED = "order_placed", "Your order was confirmed"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=30, choices=Type.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Relative frontend path, e.g. "/projects/my-app" or "/dashboard"
    link = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"Notification({self.type}) -> {self.recipient_id}: {self.title}"
