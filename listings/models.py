from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"
        REMOVED = "removed", "Removed"

    class LicenseType(models.TextChoices):
        MIT = "mit", "MIT"
        APACHE_2 = "apache2", "Apache 2.0"
        GPL_3 = "gpl3", "GPL 3.0"
        PROPRIETARY = "proprietary", "Proprietary"
        OTHER = "other", "Other"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=300, unique=True, blank=True)
    description = models.TextField()
    long_description = models.TextField(
        blank=True,
        null=True,
        help_text="Detailed markdown description shown on the project detail page",
    )
    github_repo_full_name = models.CharField(max_length=255)  # e.g. "owner/repo"
    github_default_branch = models.CharField(max_length=100, default="main")
    price = models.DecimalField(max_digits=12, decimal_places=2)  # UZS

    tags = models.JSONField(default=list, blank=True)
    cover_image = models.TextField(null=True, blank=True)
    # Seller-customizable presentation --------------------------------------
    banner_image = models.TextField(
        null=True,
        blank=True,
        help_text="Wide hero banner shown at the top of the product page",
    )
    accent_color = models.CharField(
        max_length=7,
        default="#3fd68c",
        blank=True,
        help_text="Hex color (e.g. #3fd68c) used to theme the product page",
    )
    highlights = models.JSONField(
        default=list,
        blank=True,
        help_text="List of short selling-point strings shown as bullets",
    )
    featured = models.BooleanField(
        default=False,
        help_text="Pin to the top of the marketplace as an editor's pick",
    )
    # /presentation ----------------------------------------------------------
    screenshots = models.JSONField(default=list, blank=True)  # list of URLs
    demo_url = models.URLField(max_length=1000, null=True, blank=True)
    tech_stack = models.JSONField(default=list, blank=True)  # list of strings
    license_type = models.CharField(
        max_length=20, choices=LicenseType.choices, default=LicenseType.PROPRIETARY
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)

    view_count = models.PositiveIntegerField(
        default=0, help_text="Incremented when project detail page is viewed"
    )
    download_count = models.PositiveIntegerField(
        default=0, help_text="Incremented when a buyer downloads the project archive"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the seller accepted the terms during submission",
    )
    average_rating = models.FloatField(
        default=0.0, help_text="Cached average rating score (0.0 if no ratings)"
    )
    rating_count = models.PositiveIntegerField(default=0, help_text="Cached count of ratings")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["seller", "status"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["featured"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)
            slug = base
            qs = Project.objects.exclude(pk=self.pk)
            n = 1
            while qs.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_editable(self) -> bool:
        """
        A project can only be freely edited while it's in draft or rejected status.
        Once pending_review or published, direct edits are blocked.
        Reason: we snapshot the repo at publish time, so silent edits would silently
        change what buyers already paid for. A new version cycle is the only approved
        path to updating a published listing.
        """
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)


class Rating(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ratings",
    )
    score = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    review_text = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "user")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} rated {self.project.title} {self.score}/5"


class Comment(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.user.username} on {self.project.title}"


class ProjectQA(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="qa_threads")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_questions",
    )
    question = models.TextField()
    answer = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Q&A on {self.project.title} by {self.user.username}"


class ProjectSnapshot(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="snapshots")
    version = models.PositiveIntegerField(editable=False)
    archive = models.FileField(
        upload_to="snapshots/%Y/%m/%d/",
        help_text="Compressed archive of the project source code",
    )
    file_size = models.PositiveIntegerField(
        null=True, blank=True, help_text="Size of the archive in bytes"
    )
    checksum = models.CharField(
        max_length=64, null=True, blank=True, help_text="SHA-256 checksum of the archive"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        unique_together = [("project", "version")]

    def __str__(self):
        return f"{self.project.title} v{self.version}"


def update_project_rating_cache(project):
    """Recompute average_rating and rating_count for a project from its ratings."""
    from django.db.models import Avg, Count

    stats = Rating.objects.filter(project=project).aggregate(avg=Avg("score"), count=Count("id"))
    Project.objects.filter(pk=project.pk).update(
        average_rating=stats["avg"] or 0.0,
        rating_count=stats["count"] or 0,
    )
