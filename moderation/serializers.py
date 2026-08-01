from rest_framework import serializers

from .models import ModerationLog, Report


class ReportSerializer(serializers.ModelSerializer):
    reporter_username = serializers.CharField(source="reporter.username", read_only=True)
    project_title = serializers.CharField(source="project.title", read_only=True, allow_null=True)
    reported_user_username = serializers.CharField(
        source="reported_user.username", read_only=True, allow_null=True
    )

    class Meta:
        model = Report
        fields = (
            "id",
            "reporter",
            "reporter_username",
            "project",
            "project_title",
            "reported_user",
            "reported_user_username",
            "reason",
            "detail",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "reporter",
            "reporter_username",
            "project_title",
            "reported_user_username",
            "status",
            "created_at",
            "updated_at",
        )


class ReportAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ("status",)
        read_only_fields = ()


class ModerationLogSerializer(serializers.ModelSerializer):
    admin_username = serializers.CharField(source="admin.username", read_only=True)
    target_user_username = serializers.CharField(
        source="target_user.username", read_only=True, allow_null=True
    )
    target_project_title = serializers.CharField(
        source="target_project.title", read_only=True, allow_null=True
    )

    class Meta:
        model = ModerationLog
        fields = (
            "id",
            "admin",
            "admin_username",
            "action",
            "target_user",
            "target_user_username",
            "target_project",
            "target_project_title",
            "report",
            "reason",
            "created_at",
        )
        read_only_fields = fields
