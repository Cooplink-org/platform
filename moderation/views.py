from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from listings.models import Project

from .models import ModerationLog, Report
from .serializers import ModerationLogSerializer, ReportAdminUpdateSerializer, ReportSerializer

User = get_user_model()


class ReportPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _require_staff(user):
    if not user.is_staff:
        return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
    return None


# ── user-facing report endpoints ──────────────────────────────────────────


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_report(request):
    """
    POST /api/moderation/reports/
    Create a report against a project OR a user (not both).
    """
    project_id = request.data.get("project")
    reported_user_id = request.data.get("reported_user")

    if bool(project_id) == bool(reported_user_id):
        return Response(
            {"detail": "Exactly one of 'project' or 'reported_user' must be provided."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Resolve target to check self-report
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        if project.seller == request.user:
            return Response(
                {"detail": "You cannot report your own project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        reported_user = get_object_or_404(User, pk=reported_user_id)
        if reported_user == request.user:
            return Response(
                {"detail": "You cannot report yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    data = request.data.copy()
    reason_map = {
        "infringement": Report.Reason.COPYRIGHT,
        "malicious": Report.Reason.MALICIOUS_CODE,
    }
    if data.get("reason") in reason_map:
        data["reason"] = reason_map[data["reason"]]

    serializer = ReportSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    report = serializer.save(reporter=request.user)
    return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_reports(request):
    """
    GET /api/moderation/reports/mine/
    List the authenticated user's reports, paginated.
    """
    qs = (
        Report.objects.filter(reporter=request.user)
        .select_related("reporter", "project", "reported_user")
        .order_by("-created_at")
    )
    paginator = ReportPagination()
    page = paginator.paginate_queryset(qs, request)
    if page is not None:
        serializer = ReportSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    serializer = ReportSerializer(qs, many=True)
    return Response(serializer.data)


# ── admin moderation endpoints ────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_report_list(request):
    """
    GET /api/moderation/admin/reports/
    Staff-only. List all reports, paginated, newest first.
    """
    error = _require_staff(request.user)
    if error:
        return error

    qs = (
        Report.objects.all()
        .select_related("reporter", "project", "project__seller", "reported_user")
        .order_by("-created_at")
    )
    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = ReportPagination()
    page = paginator.paginate_queryset(qs, request)
    if page is not None:
        serializer = ReportSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    serializer = ReportSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def admin_report_update(request, pk):
    """
    PATCH /api/moderation/admin/reports/{id}/
    Staff-only. Update report status (reviewed, dismissed, actioned).
    """
    error = _require_staff(request.user)
    if error:
        return error

    report = get_object_or_404(Report, pk=pk)
    serializer = ReportAdminUpdateSerializer(report, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    ModerationLog.objects.create(
        admin=request.user,
        action=ModerationLog.Action.ACTION_REPORT
        if report.status == Report.Status.ACTIONED
        else ModerationLog.Action.DISMISS_REPORT,
        report=report,
        reason=request.data.get("reason", ""),
    )

    return Response(ReportSerializer(report).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_ban_user(request, pk):
    """
    POST /api/moderation/admin/users/{id}/ban/
    Staff-only. Ban a user — set is_active=False, blacklist all JWT tokens.
    """
    error = _require_staff(request.user)
    if error:
        return error

    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        return Response(
            {"detail": "Cannot ban an admin user."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.is_active = False
    user.save(update_fields=["is_active"])

    # Blacklist all outstanding refresh tokens for this user
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)

    ModerationLog.objects.create(
        admin=request.user,
        action=ModerationLog.Action.BAN_USER,
        target_user=user,
        reason=request.data.get("reason", ""),
    )

    return Response({"detail": f"User '{user.username}' banned."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_unban_user(request, pk):
    """
    POST /api/moderation/admin/users/{id}/unban/
    Staff-only. Unban a user — set is_active=True.
    """
    error = _require_staff(request.user)
    if error:
        return error

    user = get_object_or_404(User, pk=pk)
    user.is_active = True
    user.save(update_fields=["is_active"])

    ModerationLog.objects.create(
        admin=request.user,
        action=ModerationLog.Action.UNBAN_USER,
        target_user=user,
        reason=request.data.get("reason", ""),
    )

    return Response({"detail": f"User '{user.username}' unbanned."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_delete_project(request, pk):
    """
    POST /api/moderation/admin/projects/{id}/delete/
    Staff-only. Soft-delete a project — set status to 'removed'.
    """
    error = _require_staff(request.user)
    if error:
        return error

    project = get_object_or_404(Project, pk=pk)
    project.status = Project.Status.REMOVED
    project.save(update_fields=["status"])

    ModerationLog.objects.create(
        admin=request.user,
        action=ModerationLog.Action.DELETE_PROJECT,
        target_project=project,
        reason=request.data.get("reason", ""),
    )

    return Response({"detail": f"Project '{project.title}' removed."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_restore_project(request, pk):
    """
    POST /api/moderation/admin/projects/{id}/restore/
    Staff-only. Restore a soft-deleted project — set status back to 'published'.
    """
    error = _require_staff(request.user)
    if error:
        return error

    project = get_object_or_404(Project, pk=pk)
    if project.status != Project.Status.REMOVED:
        return Response(
            {"detail": "Only removed projects can be restored."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    project.status = Project.Status.PUBLISHED
    project.save(update_fields=["status"])

    ModerationLog.objects.create(
        admin=request.user,
        action=ModerationLog.Action.RESTORE_PROJECT,
        target_project=project,
        reason=request.data.get("reason", ""),
    )

    return Response({"detail": f"Project '{project.title}' restored and published."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_moderation_log(request):
    """
    GET /api/moderation/admin/log/
    Staff-only. View the full moderation audit log, paginated, newest first.
    """
    error = _require_staff(request.user)
    if error:
        return error

    qs = (
        ModerationLog.objects.all()
        .select_related("admin", "target_user", "target_project", "report")
        .order_by("-created_at")
    )
    paginator = ReportPagination()
    page = paginator.paginate_queryset(qs, request)
    if page is not None:
        serializer = ModerationLogSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    serializer = ModerationLogSerializer(qs, many=True)
    return Response(serializer.data)


# ── admin user list ──────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_user_list(request):
    """
    GET /api/moderation/admin/users/
    Staff-only. List all users, with optional search.
    """
    error = _require_staff(request.user)
    if error:
        return error

    user_model = get_user_model()
    qs = user_model.objects.all().order_by("-date_joined")
    q = request.query_params.get("q")
    if q:
        qs = qs.filter(username__icontains=q)

    data = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_staff": u.is_staff,
            "is_active": u.is_active,
            "date_joined": u.date_joined,
            "created_at": u.created_at,
        }
        for u in qs[:200]
    ]
    return Response(data)


# ── admin project list ───────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_project_list(request):
    """
    GET /api/moderation/admin/projects/
    Staff-only. List all projects, with optional status filter.
    """
    error = _require_staff(request.user)
    if error:
        return error

    qs = Project.objects.all().select_related("seller").order_by("-created_at")
    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    data = [
        {
            "id": p.id,
            "title": p.title,
            "slug": p.slug,
            "status": p.status,
            "price": str(p.price),
            "seller_username": p.seller.username if p.seller else None,
            "created_at": p.created_at,
        }
        for p in qs[:200]
    ]
    return Response(data)
