from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from unfold.admin import ModelAdmin

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


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("title", "seller", "_status_formatted", "price", "_github_link", "created_at")
    list_filter = ("status", "category")
    search_fields = ("title", "seller__username", "github_repo_full_name")
    readonly_fields = ("slug", "version", "created_at", "updated_at", "terms_accepted_at")
    ordering = ("-created_at",)
    autocomplete_fields = ["seller", "category"]
    inlines = [ProjectSnapshotInline]

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
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.get_status_display()
        )

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
        ]
        return custom + base

    @admin.action(description="Approve selected projects")
    def approve_selected(self, request, queryset):
        count = 0
        for project in queryset.filter(status=Project.Status.PENDING_REVIEW):
            project.status = Project.Status.PUBLISHED
            project.save(update_fields=["status"])
            
            # Notify seller
            notify_user_task.delay(
                project.seller_id,
                "listing_approved",
                {"title": project.title}
            )
            count += 1
            
        self.message_user(request, f"{count} project(s) approved and live.")

    def reject_view(self, request, object_id):
        project = self.get_object(request, object_id)
        if request.method == "POST":
            reason = request.POST.get("reason", "No reason provided.")
            project.status = Project.Status.REJECTED
            project.save(update_fields=["status"])
            
            # Notify seller
            notify_user_task.delay(
                project.seller_id,
                "listing_rejected",
                {"title": project.title, "reason": reason}
            )
            
            self.message_user(request, f"Project '{project.title}' rejected.")
            return HttpResponseRedirect(reverse("admin:listings_project_changelist"))

        return render(request, "admin/listings/reject_confirm.html", {
            "project": project,
            "opts": self.model._meta,
        })

    actions = ["approve_selected"]


@admin.register(ProjectSnapshot)
class ProjectSnapshotAdmin(ModelAdmin):
    list_display = ("project", "version", "file_size", "created_at")
    readonly_fields = ("version", "created_at")
    search_fields = ("project__title",)
