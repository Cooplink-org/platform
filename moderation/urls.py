from django.urls import path

from . import views

urlpatterns = [
    # User-facing report endpoints
    path("reports/", views.create_report, name="create_report"),
    path("reports/mine/", views.my_reports, name="my_reports"),
    # Admin moderation endpoints
    path("admin/reports/", views.admin_report_list, name="admin_report_list"),
    path("admin/reports/<int:pk>/", views.admin_report_update, name="admin_report_update"),
    path("admin/users/", views.admin_user_list, name="admin_user_list"),
    path("admin/users/<int:pk>/ban/", views.admin_ban_user, name="admin_ban_user"),
    path("admin/users/<int:pk>/unban/", views.admin_unban_user, name="admin_unban_user"),
    path("admin/projects/", views.admin_project_list, name="admin_project_list"),
    path(
        "admin/projects/<int:pk>/delete/", views.admin_delete_project, name="admin_delete_project"
    ),
    path(
        "admin/projects/<int:pk>/restore/",
        views.admin_restore_project,
        name="admin_restore_project",
    ),
    path("admin/log/", views.admin_moderation_log, name="admin_moderation_log"),
]
