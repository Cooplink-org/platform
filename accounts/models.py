from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    github_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    github_username = models.CharField(max_length=255, null=True, blank=True)
    avatar_url = models.URLField(max_length=1000, null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    is_seller = models.BooleanField(default=False)
    # Encrypted with Fernet; null until the user completes the repo OAuth flow.
    github_token_encrypted = models.TextField(null=True, blank=True)
    telegram_chat_id = models.CharField(max_length=100, null=True, blank=True, help_text="Optional chat ID for notifications")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.username or self.email or str(self.id)
