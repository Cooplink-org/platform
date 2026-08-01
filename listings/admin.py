from django.contrib import admin
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from unfold.admin import ModelAdmin

from moderation.ai_reviewer import run_ai_review_for_project
from moderation.models import AICodeReview
from notifications.tasks import notify_user_task

from .models import Category, Project, ProjectSnapshot


class ProjectSnapshotInline(admin.TabularInline):
    """Display project snapshots."""

    model = ProjectSnapshot
    fields = ("version", "file_size", "created_at")
    readonly_fields = ("version", "created_at")
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("-version",)


class AICodeReviewInline(admin.TabularInline):
    """Display AI code review results."""

    model = AICodeReview
    fields = (
        "status",
        "is_malware",
        "malware_score",
        "match_percentage",
        "model_used",
        "tokens_used",
        "reviewed_at",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    ordering = ("-reviewed_at",)


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = (
        "title",
        "seller",
        "_status_formatted",
        "_ai_review_status",
        "price",
        "_snapshot_status",
        "_github_link",
        "created_at",
    )
    list_filter = ("status", "category")
    search_fields = ("title", "seller__username", "github_repo_full_name")
    readonly_fields = ("slug", "version", "created_at", "updated_at", "terms_accepted_at")
    ordering = ("-created_at",)
    autocomplete_fields = ["seller", "category"]
    inlines = [ProjectSnapshotInline, AICodeReviewInline]

    @admin.display(description="Status")
    def _status_formatted(self, obj):
        from django.utils.html import format_html

        colors = {
            Project.Status.PUBLISHED: "green",
            Project.Status.PENDING_REVIEW: "orange",
            Project.Status.DRAFT: "gray",
            Project.Status.REJECTED: "red",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>', color, obj.get_status_display()
        )

    @admin.display(description="AI Code Review")
    def _ai_review_status(self, obj):
        from django.utils.html import format_html

        latest_review = AICodeReview.objects.filter(project=obj).order_by("-reviewed_at").first()
        review_url = reverse("admin:listings_project_ai_review", args=[obj.pk])

        if not latest_review:
            return format_html(
                '<a href="{}" style="background-color: #3b82f6; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; text-decoration: none; font-weight: bold;">⚡ Run AI Review</a>',  # noqa: E501
                review_url,
            )

        badge_colors = {
            AICodeReview.Status.PASSED: ("#16a34a", f"✓ Safe ({latest_review.match_percentage}%)"),
            AICodeReview.Status.FLAGGED_MALWARE: (
                "#dc2626",
                f"⚠ Malware ({latest_review.malware_score}%)",
            ),
            AICodeReview.Status.DESCRIPTION_MISMATCH: (
                "#d97706",
                f"⚡ Mismatch ({latest_review.match_percentage}%)",
            ),
            AICodeReview.Status.ERROR: ("#6b7280", "✗ Error"),
        }
        bg, text = badge_colors.get(
            latest_review.status, ("#6b7280", latest_review.get_status_display())
        )

        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-right: 4px;">{}</span>'  # noqa: E501
            '<a href="{}" title="Re-run AI review" style="color: #6b7280; text-decoration: none; font-size: 11px;">↻</a>',  # noqa: E501
            bg,
            text,
            review_url,
        )

    @admin.display(description="Snapshot")
    def _snapshot_status(self, obj):
        from django.utils.html import format_html

        has = ProjectSnapshot.objects.filter(project=obj).exists()
        if has:
            snap = ProjectSnapshot.objects.filter(project=obj).order_by("-version").first()
            return format_html('<span style="color: green;">v{} ✓</span>', snap.version)
        return format_html('<span style="color: red;">✗ none</span>')

    @admin.display(description="Source")
    def _github_link(self, obj):
        from django.utils.html import format_html

        url = f"https://github.com/{obj.github_repo_full_name}"
        return format_html('<a href="{}" target="_blank">view repo ↗</a>', url)

    def get_urls(self):
        base = super().get_urls()
        custom = [
            path(
                "<path:object_id>/reject/",
                self.admin_site.admin_view(self.reject_view),
                name="listings_project_reject",
            ),
            path(
                "<path:object_id>/ai-review/",
                self.admin_site.admin_view(self.ai_review_trigger_view),
                name="listings_project_ai_review",
            ),
        ]
        return custom + base

    def ai_review_trigger_view(self, request, object_id):
        project = self.get_object(request, object_id)
        if not project:
            self.message_user(request, "Project not found.", level="ERROR")
            return HttpResponseRedirect(reverse("admin:listings_project_changelist"))

        try:
            review = run_ai_review_for_project(project.id, user=request.user)
            if review.status == AICodeReview.Status.FLAGGED_MALWARE:
                self.message_user(
                    request,
                    f"AI Review FLAGGED MALWARE on '{project.title}' "
                    f"(Malware Score: {review.malware_score}%). Model: {review.model_used}",
                    level="ERROR",
                )
            elif review.status == AICodeReview.Status.DESCRIPTION_MISMATCH:
                self.message_user(
                    request,
                    f"AI Review flagged description mismatch on '{project.title}' "
                    f"(Match Score: {review.match_percentage}%). Model: {review.model_used}",
                    level="WARNING",
                )
            elif review.status == AICodeReview.Status.ERROR:
                self.message_user(
                    request,
                    f"AI Review failed for '{project.title}': {review.summary}",
                    level="ERROR",
                )
            else:
                self.message_user(
                    request,
                    f"AI Review PASSED for '{project.title}' "
                    f"(Match: {review.match_percentage}%, Malware Score: {review.malware_score}%). "
                    f"Model: {review.model_used}, Tokens: {review.tokens_used}",
                    level="SUCCESS",
                )
        except Exception as exc:
            self.message_user(request, f"AI Review error for {project.title}: {exc}", level="ERROR")

        referer = request.META.get("HTTP_REFERER")
        if referer and "ai-review" not in referer:
            return HttpResponseRedirect(referer)
        return HttpResponseRedirect(reverse("admin:listings_project_changelist"))

    @admin.action(description="Run AI Code Review on selected projects")
    def run_ai_code_review(self, request, queryset):
        passed = 0
        malware = 0
        mismatch = 0
        errors = 0

        for project in queryset:
            try:
                review = run_ai_review_for_project(project.id, user=request.user)
                if review.status == AICodeReview.Status.FLAGGED_MALWARE:
                    malware += 1
                elif review.status == AICodeReview.Status.DESCRIPTION_MISMATCH:
                    mismatch += 1
                elif review.status == AICodeReview.Status.ERROR:
                    errors += 1
                else:
                    passed += 1
            except Exception:
                errors += 1

        self.message_user(
            request,
            f"AI Review completed for {queryset.count()} project(s): {passed} Passed, "
            f"{malware} Malware Flagged, {mismatch} Mismatches, {errors} Errors.",
            level="SUCCESS" if malware == 0 and errors == 0 else "WARNING",
        )

    @admin.action(description="Approve selected projects")
    def approve_selected(self, request, queryset):
        from .tasks import build_project_snapshot

        count = 0
        for project in queryset.filter(status=Project.Status.PENDING_REVIEW):
            project.status = Project.Status.PUBLISHED
            project.save(update_fields=["status"])

            try:
                build_project_snapshot(project.id)
            except Exception as exc:
                self.message_user(
                    request, f"Snapshot failed for {project.title}: {exc}", level="WARNING"
                )

            notify_user_task.delay(project.seller_id, "listing_approved", {"title": project.title})
            count += 1

        self.message_user(request, f"{count} project(s) approved and live.")

    @admin.action(description="Create / refresh snapshot for selected projects")
    def create_snapshot(self, request, queryset):
        from .tasks import build_project_snapshot

        ok = 0
        failed = 0
        for project in queryset:
            try:
                build_project_snapshot(project.id)
                ok += 1
            except Exception as exc:
                self.message_user(
                    request,
                    f"Snapshot failed for {project.title} (id={project.id}): {exc}",
                    level="WARNING",
                )
                failed += 1
        if ok:
            self.message_user(request, f"Snapshot created for {ok} project(s).")
        if failed:
            self.message_user(
                request, f"Failed for {failed} project(s). See warnings.", level="WARNING"
            )

    def reject_view(self, request, object_id):
        project = self.get_object(request, object_id)
        if request.method == "POST":
            reason = request.POST.get("reason", "No reason provided.")
            project.status = Project.Status.REJECTED
            project.save(update_fields=["status"])

            notify_user_task.delay(
                project.seller_id, "listing_rejected", {"title": project.title, "reason": reason}
            )

            self.message_user(request, f"Project '{project.title}' rejected.")
            return HttpResponseRedirect(reverse("admin:listings_project_changelist"))

        return render(
            request,
            "admin/listings/reject_confirm.html",
            {
                "project": project,
                "opts": self.model._meta,
            },
        )

    actions = ["run_ai_code_review", "approve_selected", "create_snapshot"]


@admin.register(ProjectSnapshot)
class ProjectSnapshotAdmin(ModelAdmin):
    list_display = ("project", "version", "file_size", "created_at")
    readonly_fields = ("version", "created_at")
    search_fields = ("project__title",)
