from decimal import Decimal

from django.conf import settings
from django.db import models


class LeaderboardSettings(models.Model):
    """Singleton admin-configurable settings for the Crack It leaderboard."""

    enabled = models.BooleanField(
        default=True,
        help_text="Master switch for the leaderboard page and submissions.",
    )
    min_amount_uzs = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("10000.00"),
        help_text="Minimum bid (UZS) a brand must pay to join the leaderboard.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leaderboard settings"
        verbose_name_plural = "Leaderboard settings"

    def __str__(self):
        return "Leaderboard settings"

    def save(self, *args, **kwargs):
        # Singleton: always update pk=1 instead of creating new rows.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LeaderboardEntry(models.Model):
    """A brand competing for a place on the Crack It leaderboard.

    Rank is not stored — it is always derived from the paid entries,
    so a higher payment instantly takes a higher place.
    """

    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    class Category(models.TextChoices):
        TECH = "tech", "Texnologiya va startaplar"
        TRADE = "trade", "Savdo va xizmatlar"
        MEDIA = "media", "Shaxslar va media"
        EDU = "edu", "Ta'lim va karyera"
        AI = "ai", "AI"

    domain = models.CharField(max_length=255)
    brand_name = models.CharField(max_length=120)
    description = models.CharField(max_length=280, blank=True)
    logo_url = models.URLField(blank=True)
    amount_uzs = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    category = models.CharField(
        max_length=10,
        choices=Category.choices,
        default=Category.TECH,
        help_text="Used by the frontend category tabs/filter.",
    )

    # Engagement metrics shown on the leaderboard.
    likes = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Outbound visits to the entry's domain (incremented by the public click endpoint)."
        ),
    )

    payment_ref = models.CharField(max_length=255, null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leaderboard_entries",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-amount_uzs", "paid_at", "id"]
        verbose_name = "Leaderboard entry"
        verbose_name_plural = "Leaderboard entries"

    def __str__(self):
        return f"#{self.id} {self.brand_name} ({self.amount_uzs} UZS, {self.status})"

    # ── ranking helpers ───────────────────────────────────────────────────────

    @classmethod
    def ranked(cls):
        """Paid entries in leaderboard order: higher amount first, earlier payer wins ties."""
        return list(
            cls.objects.filter(status=cls.Status.PAID).order_by("-amount_uzs", "paid_at", "id")
        )

    @classmethod
    def prospective_position(cls, amount):
        """1-based position a new bid of `amount` UZS would take once paid.

        Equal amounts keep their existing order — the newcomer sits right
        below the entries that paid the same amount first.
        """
        return (
            cls.objects.filter(status=cls.Status.PAID, amount_uzs__gt=amount).count()
            + cls.objects.filter(status=cls.Status.PAID, amount_uzs=amount).count()
            + 1
        )

    @classmethod
    def total_earned(cls):
        """Total UZS collected from paid entries since the leaderboard started."""
        total = cls.objects.filter(status=cls.Status.PAID).aggregate(
            total=models.Sum("amount_uzs")
        )["total"]
        return (total or Decimal("0.00")).quantize(Decimal("1.00"))

    @classmethod
    def started_at(cls):
        first = (
            cls.objects.filter(status=cls.Status.PAID)
            .order_by("paid_at")
            .values_list("paid_at", flat=True)
            .first()
        )
        return first
