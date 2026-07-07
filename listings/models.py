from django.conf import settings
from django.db import models
from django.utils.text import slugify
import uuid


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
    github_repo_full_name = models.CharField(max_length=255)   # e.g. "owner/repo"
    github_default_branch = models.CharField(max_length=100, default="main")
    price = models.DecimalField(max_digits=12, decimal_places=2)  # UZS

    tags = models.JSONField(default=list, blank=True)
    cover_image = models.URLField(max_length=1000, null=True, blank=True)
    screenshots = models.JSONField(default=list, blank=True)   # list of URLs
    demo_url = models.URLField(max_length=1000, null=True, blank=True)
    tech_stack = models.JSONField(default=list, blank=True)    # list of strings
    license_type = models.CharField(
        max_length=20, choices=LicenseType.choices, default=LicenseType.PROPRIETARY
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    version = models.PositiveIntegerField(default=1)

    view_count = models.PositiveIntegerField(default=0, help_text="Incremented when project detail page is viewed")
    download_count = models.PositiveIntegerField(default=0, help_text="Incremented when a buyer downloads the project archive")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when the seller accepted the terms during submission")

    class Meta:
        ordering = ["-created_at"]

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


class ProjectSnapshot(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="snapshots"
    )
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
