from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Report(models.Model):
    class Reason(models.TextChoices):
        COPYRIGHT = "copyright", "Copyright Violation"
        MALICIOUS_CODE = "malicious_code", "Malicious Code"
        MISLEADING = "misleading", "Misleading Description"
        DUPLICATE = "duplicate", "Duplicate Listing"
        SPAM = "spam", "Spam"
        INAPPROPRIATE = "inappropriate", "Inappropriate"
        FRAUD = "fraud", "Fraud"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    project = models.ForeignKey(
        "listings.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports_on_user",
    )
    reason = models.CharField(max_length=30, choices=Reason.choices)
    detail = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.project.title if self.project else self.reported_user.username
        return f"Report #{self.pk} — {self.reason} on {target}"

    def clean(self):
        if bool(self.project_id) == bool(self.reported_user_id):
            raise ValidationError("Exactly one of project or reported_user must be set.")
        if self.reason == self.Reason.OTHER and not self.detail:
            raise ValidationError({"detail": "Detail is required when reason is 'other'."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ModerationLog(models.Model):
    class Action(models.TextChoices):
        BAN_USER = "ban_user", "Ban User"
        UNBAN_USER = "unban_user", "Unban User"
        DELETE_PROJECT = "delete_project", "Delete Project"
        RESTORE_PROJECT = "restore_project", "Restore Project"
        DISMISS_REPORT = "dismiss_report", "Dismiss Report"
        ACTION_REPORT = "action_report", "Action Report"

    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="moderation_actions",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_targets",
    )
    target_project = models.ForeignKey(
        "listings.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    report = models.ForeignKey(
        Report,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
    )
    reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.admin} at {self.created_at}"


class AICodeReview(models.Model):
    class Status(models.TextChoices):
        PASSED = "passed", "Passed (Safe & Matching)"
        FLAGGED_MALWARE = "flagged_malware", "Flagged Malware"
        DESCRIPTION_MISMATCH = "description_mismatch", "Description Mismatch"
        ERROR = "error", "Analysis Error"

    project = models.ForeignKey(
        "listings.Project",
        on_delete=models.CASCADE,
        related_name="ai_code_reviews",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PASSED,
    )
    is_malware = models.BooleanField(
        default=False,
        help_text="True if malicious patterns, obfuscation, or backdoors were detected",
    )
    malware_score = models.IntegerField(
        default=0,
        help_text="Malware risk score from 0 (clean) to 100 (high severity malware)",
    )
    match_percentage = models.IntegerField(
        default=100,
        help_text="Match score (0-100%) between code logic and project title/description",
    )
    summary = models.TextField(
        blank=True,
        null=True,
        help_text="Concise summary of AI review findings",
    )
    malware_findings = models.JSONField(
        default=list,
        blank=True,
        help_text="List of specific suspicious code patterns or threats detected",
    )
    description_analysis = models.TextField(
        blank=True,
        null=True,
        help_text="Explanation of description match score",
    )
    model_used = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Model Studio model that successfully performed the review",
    )
    tokens_used = models.PositiveIntegerField(
        default=0,
        help_text="Total tokens consumed for prompt and completion",
    )
    raw_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw structured JSON response returned by the AI",
    )
    reviewed_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_code_reviews_initiated",
    )

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self):
        return (
            f"AI Review for {self.project.title} "
            f"({self.get_status_display()}) at {self.reviewed_at}"
        )
