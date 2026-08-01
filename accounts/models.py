from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models

phone_validator = RegexValidator(
    regex=r"^\+?1?\d{7,15}$",
    message="Phone number must be 7-15 digits, optionally starting with '+' or '1'.",
)


class User(AbstractUser):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                "phone_number",
                condition=models.Q(phone_verified=True),
                name="unique_verified_phone",
            ),
        ]

    github_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    github_username = models.CharField(max_length=255, null=True, blank=True)
    avatar_url = models.TextField(null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    is_seller = models.BooleanField(default=False)
    # Encrypted with Fernet; null until the user completes the repo OAuth flow.
    github_token_encrypted = models.TextField(null=True, blank=True)
    telegram_chat_id = models.CharField(
        max_length=100, null=True, blank=True, help_text="Optional chat ID for notifications"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Onboarding fields
    full_legal_name = models.CharField(max_length=255, blank=True, default="")
    phone_number = models.CharField(
        max_length=20, blank=True, default="", validators=[phone_validator]
    )
    phone_verified = models.BooleanField(
        default=False,
        help_text="True when phone_number was verified via Telegram contact sharing.",
    )
    phone_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent phone verification.",
    )
    terms_accepted_version = models.CharField(max_length=20, blank=True, default="")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    # IP tracking
    last_login_ip = models.GenericIPAddressField(
        null=True, blank=True, help_text="IP address of the most recent login"
    )
    signup_ip = models.GenericIPAddressField(
        null=True, blank=True, help_text="IP address at account creation"
    )

    def __str__(self):
        return self.username or self.email or str(self.id)

    @property
    def is_onboarded(self) -> bool:
        return (
            bool(self.full_legal_name)
            and bool(self.phone_number)
            and self.terms_accepted_version == getattr(settings, "CURRENT_TERMS_VERSION", "")
            and self.terms_accepted_at is not None
        )
