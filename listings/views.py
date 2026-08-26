import logging

import requests
from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db import connection, models
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.utils import decrypt_token

from .models import Category, Comment, Project, ProjectQA, Rating
from .serializers import (
    CategorySerializer,
    CommentSerializer,
    ProjectQASerializer,
    ProjectSerializer,
    PublicProjectListSerializer,
    PublicProjectSerializer,
    RatingSerializer,
)

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────


def _require_seller(user):
    """Return None if user is a seller, or a 403 Response otherwise."""
    if not user.is_seller or not user.github_token_encrypted:
        return Response(
            {
                "detail": "You must complete the seller GitHub authorization first. "
                "Visit /api/auth/github/connect-repos/ to enable seller features."
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


# ── my-repos ──────────────────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_repos(request):
    """
    GET /api/listings/my-repos/
    Returns the authenticated seller's GitHub repositories.
    Requires the user to have completed the repo OAuth flow (is_seller=True with
    an encrypted token stored).
    """
    error = _require_seller(request.user)
    if error:
        return error

    try:
        gh_token = decrypt_token(request.user.github_token_encrypted)
    except Exception:
        return Response(
            {"detail": "Failed to decrypt GitHub token. Please re-authorize."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        resp = requests.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            params={"per_page": 100, "sort": "updated"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return Response(
            {"detail": f"GitHub API error: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    repos = [
        {
            "name": r["name"],
            "full_name": r["full_name"],
            "description": r.get("description"),
            "default_branch": r.get("default_branch", "main"),
            "private": r["private"],
            "updated_at": r.get("updated_at"),
            "size": r.get("size"),
        }
        for r in resp.json()
    ]
    return Response(repos)


# ── project CRUD ──────────────────────────────────────────────────────────────


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def project_list_create(request):
    """
    GET  /api/listings/projects/  — list the authenticated seller's own projects.
    POST /api/listings/projects/  — create a new project (status starts at draft).
    """
    error = _require_seller(request.user)
    if error:
        return error

    if request.method == "GET":
        qs = Project.objects.filter(seller=request.user).order_by("-created_at")
        return Response(ProjectSerializer(qs, many=True).data)

    serializer = ProjectSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    project = serializer.save(seller=request.user, status=Project.Status.DRAFT)
    return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, pk):
    """
    GET    /api/listings/projects/{id}/  — retrieve a project.
    PATCH  /api/listings/projects/{id}/  — update fields (draft/rejected/published).
    DELETE /api/listings/projects/{id}/  — delete a project (draft/rejected only).

    PATCH is blocked for pending_review (under review) and suspended/removed
    projects.  Deleting is only allowed for draft/rejected since published
    projects have orders referencing them.
    """
    error = _require_seller(request.user)
    if error:
        return error

    try:
        project = Project.objects.get(pk=pk, seller=request.user)
    except Project.DoesNotExist:
        return Response({"detail": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(ProjectSerializer(project).data)

    if request.method == "PATCH":
        if project.status in (
            Project.Status.PENDING_REVIEW,
            Project.Status.SUSPENDED,
            Project.Status.REMOVED,
        ):
            return Response(
                {"detail": f"Project is {project.get_status_display()} and cannot be edited."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ProjectSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectSerializer(project).data)

    # DELETE — only draft/rejected
    if not project.is_editable:
        return Response(
            {
                "detail": f"Project is {project.get_status_display()} and cannot be deleted. "
                "Set it to draft first."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    project.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def project_submit(request, pk):
    """
    POST /api/listings/projects/{id}/submit/
    Moves a project from draft → pending_review so staff can review it.
    Only allowed from draft status.
    """
    error = _require_seller(request.user)
    if error:
        return error

    try:
        project = Project.objects.get(pk=pk, seller=request.user)
    except Project.DoesNotExist:
        return Response({"detail": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

    if project.status != Project.Status.DRAFT:
        return Response(
            {
                "detail": (
                    f"Only draft projects can be submitted. Current status: "
                    f"{project.get_status_display()}."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    from django.utils import timezone

    if not request.data.get("accept_terms"):
        return Response(
            {"detail": "You must accept the terms and conditions to submit for review."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    project.status = Project.Status.PENDING_REVIEW
    project.terms_accepted_at = timezone.now()
    project.save(update_fields=["status", "terms_accepted_at"])

    # Create snapshot synchronously (no Celery dependency)
    from .tasks import build_project_snapshot

    try:
        build_project_snapshot(project.id)
    except Exception as exc:
        log.error("Snapshot creation failed for project %s: %s", project.id, exc)

    return Response(ProjectSerializer(project).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def project_new_version(request, pk):
    """
    POST /api/listings/projects/{id}/new-version/
    Trigger a new source-code snapshot from GitHub without changing the
    project's published status.  Only allowed for published projects.
    """
    error = _require_seller(request.user)
    if error:
        return error

    try:
        project = Project.objects.get(pk=pk, seller=request.user)
    except Project.DoesNotExist:
        return Response({"detail": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

    if project.status != Project.Status.PUBLISHED:
        return Response(
            {
                "detail": (
                    f"Only published projects can get a new version. Current status: "
                    f"{project.get_status_display()}."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    project.version = models.F("version") + 1
    project.save(update_fields=["version"])
    project.refresh_from_db()

    from .tasks import build_project_snapshot

    try:
        build_project_snapshot(project.id)
    except Exception as exc:
        log.error("Snapshot creation failed for project %s: %s", project.id, exc)

    return Response(ProjectSerializer(project).data)


# ── public catalog ───────────────────────────────────────────────────────────


class ProjectPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@api_view(["GET"])
@permission_classes([AllowAny])
def category_list(_request):
    """GET /api/listings/categories/ — returns all categories."""
    cats = Category.objects.all().order_by("name")
    serializer = CategorySerializer(cats, many=True)
    return Response(serializer.data)


class PublicProjectList(generics.ListAPIView):
    """
    GET /api/listings/
    Published projects only, paginated, filterable by category/tags/price range/tech_stack,
    sortable by newest/price/popularity.
    Uses a lightweight serializer that excludes description/screenshots to reduce payload.
    Filters:
      - category: slug
      - tags: comma-separated (SQLite-compatible)
      - min_price, max_price: decimal
      - tech_stack: comma-separated (SQLite-compatible)
      - q: search by title/description (icontains for SQLite compatibility)
    Ordering:
      - created_at / -created_at (newest/oldest, default: -created_at)
      - price / -price
      - view_count / -view_count (popularity)
    """

    serializer_class = PublicProjectListSerializer
    permission_classes = [AllowAny]
    pagination_class = ProjectPagination
    filter_backends = [OrderingFilter]
    ordering = ["_featured_rank", "-created_at"]
    ordering_fields = ["created_at", "price", "view_count", "_featured_rank"]

    def get_queryset(self):
        qs = (
            Project.objects.filter(status=Project.Status.PUBLISHED)
            .select_related("category", "seller")
            .order_by("-created_at")
        )
        # Pin editor's picks to the top regardless of the chosen ordering.
        qs = qs.annotate(
            _featured_rank=models.Case(
                models.When(featured=True, then=models.Value(0)),
                default=models.Value(1),
                output_field=models.IntegerField(),
            )
        )

        category_slug = self.request.query_params.get("category")
        if category_slug:
            qs = qs.filter(category__slug=category_slug)

        tags = self.request.query_params.get("tags")
        if tags:
            for tag in (t.strip() for t in tags.split(",") if t.strip()):
                if connection.vendor == "postgresql":
                    qs = qs.filter(tags__contains=[tag])
                else:
                    qs = qs.filter(tags__icontains=tag)

        min_price = self.request.query_params.get("min_price")
        max_price = self.request.query_params.get("max_price")
        if min_price:
            qs = qs.filter(price__gte=min_price)
        if max_price:
            qs = qs.filter(price__lte=max_price)

        tech_stack = self.request.query_params.get("tech_stack")
        if tech_stack:
            for tech in (t.strip() for t in tech_stack.split(",") if t.strip()):
                if connection.vendor == "postgresql":
                    qs = qs.filter(tech_stack__contains=[tech])
                else:
                    qs = qs.filter(tech_stack__icontains=tech)

        license_type = self.request.query_params.get("license_type")
        if license_type:
            qs = qs.filter(license_type=license_type)

        featured_only = self.request.query_params.get("featured")
        if featured_only in ("1", "true", "yes"):
            qs = qs.filter(featured=True)

        q = self.request.query_params.get("q")
        if q:
            if connection.vendor == "postgresql":
                search_vector = SearchVector("title", weight="A") + SearchVector(
                    "description", weight="B"
                )
                qs = qs.annotate(search=search_vector).filter(search=SearchQuery(q))
            else:
                qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))

        return qs


@api_view(["GET"])
@permission_classes([AllowAny])
def project_detail_public(_request, slug):
    """
    GET /api/listings/{slug}/
    Detail view for a published project.
    Returns: title, description, screenshots, demo_url, tech_stack, price, seller's public profile.
    Never exposes github_repo_full_name or private repo details.
    Increments view_count on each request.
    """
    try:
        project = Project.objects.select_related("category", "seller").get(
            slug=slug, status=Project.Status.PUBLISHED
        )
    except Project.DoesNotExist:
        return Response({"detail": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

    # Increment view count
    Project.objects.filter(pk=project.pk).update(view_count=F("view_count") + 1)

    return Response(PublicProjectSerializer(project).data)


# ── comments ─────────────────────────────────────────────────────────────


class CommentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def comment_list_create(request, slug):
    """
    GET  /api/listings/{slug}/comments/  — list comments (public, paginated)
    POST /api/listings/{slug}/comments/  — create a comment (authenticated)
    """
    project = get_object_or_404(Project, slug=slug, status=Project.Status.PUBLISHED)

    if request.method == "GET":
        qs = Comment.objects.filter(project=project).select_related("user")
        paginator = CommentPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = CommentSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = CommentSerializer(qs, many=True)
        return Response(serializer.data)

    # POST
    if not request.user.is_authenticated:
        return Response(status=status.HTTP_401_UNAUTHORIZED)
    serializer = CommentSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    comment = serializer.save(project=project, user=request.user)
    return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)


# ── ratings ──────────────────────────────────────────────────────────────


class RatingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@api_view(["GET", "POST", "PATCH", "DELETE"])
@permission_classes([AllowAny])
def rating_upsert(request, slug):
    """
    GET    /api/listings/{slug}/ratings/  — list ratings (public, paginated)
    POST   /api/listings/{slug}/ratings/  — create rating (buyer only)
    PATCH  /api/listings/{slug}/ratings/  — update own rating
    DELETE /api/listings/{slug}/ratings/  — delete own rating
    """
    lookup = Q(slug=slug)
    if str(slug).isdigit():
        lookup |= Q(pk=int(slug))
    project = get_object_or_404(Project, lookup, status=Project.Status.PUBLISHED)

    if request.method == "GET":
        qs = Rating.objects.filter(project=project).select_related("user")
        paginator = RatingPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = RatingSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = RatingSerializer(qs, many=True)
        return Response(serializer.data)

    # All mutating methods require auth
    if request.method in ("POST", "PATCH", "DELETE"):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        # Verify user — must have a verified phone number
        if not request.user.phone_verified:
            return Response(
                {"detail": "Only verified users can rate or review this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Sellers cannot rate their own project
        if project.seller == request.user:
            return Response(
                {"detail": "You cannot rate your own project."},
                status=status.HTTP_403_FORBIDDEN,
            )

    try:
        existing = Rating.objects.get(project=project, user=request.user)
    except Rating.DoesNotExist:
        existing = None

    data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
    if "rating" in data and "score" not in data:
        data["score"] = data["rating"]
    if "body" in data and "review_text" not in data:
        data["review_text"] = data["body"]

    if request.method == "POST":
        if existing is not None:
            # Upsert: update the existing one
            serializer = RatingSerializer(existing, data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            from .models import update_project_rating_cache

            update_project_rating_cache(project)
            return Response(RatingSerializer(existing).data)
        serializer = RatingSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        rating = serializer.save(project=project, user=request.user)
        from .models import update_project_rating_cache

        update_project_rating_cache(project)
        return Response(RatingSerializer(rating).data, status=status.HTTP_201_CREATED)

    if request.method == "PATCH":
        if existing is None:
            return Response(
                {"detail": "You have not rated this project yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = RatingSerializer(existing, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        from .models import update_project_rating_cache

        update_project_rating_cache(project)
        return Response(RatingSerializer(existing).data)

    # DELETE
    if existing is None:
        return Response(
            {"detail": "You have not rated this project yet."},
            status=status.HTTP_404_NOT_FOUND,
        )
    existing.delete()
    from .models import update_project_rating_cache

    update_project_rating_cache(project)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ── Q&A ─────────────────────────────────────────────────────────────


class QAPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def qa_list_create(request, slug):
    """
    GET  /api/listings/{slug}/qa/  — list Q&A threads (public)
    POST /api/listings/{slug}/qa/  — ask a question (authenticated)
    """
    lookup = Q(slug=slug)
    if str(slug).isdigit():
        lookup |= Q(pk=int(slug))
    project = get_object_or_404(Project, lookup, status=Project.Status.PUBLISHED)

    if request.method == "GET":
        qs = ProjectQA.objects.filter(project=project).select_related("user")
        paginator = QAPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = ProjectQASerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = ProjectQASerializer(qs, many=True)
        return Response(serializer.data)

    # POST
    if not request.user.is_authenticated:
        return Response(
            {"detail": "Authentication required to ask a question."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    serializer = ProjectQASerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    qa = serializer.save(project=project, user=request.user)
    return Response(ProjectQASerializer(qa).data, status=status.HTTP_201_CREATED)


@api_view(["POST", "PATCH"])
@permission_classes([IsAuthenticated])
def qa_answer(request, slug, pk):
    """
    POST/PATCH /api/listings/{slug}/qa/{pk}/answer/ — seller answers a question
    """
    lookup = Q(slug=slug)
    if str(slug).isdigit():
        lookup |= Q(pk=int(slug))
    project = get_object_or_404(Project, lookup, status=Project.Status.PUBLISHED)

    if project.seller != request.user:
        return Response(
            {"detail": "Only the seller of this listing can answer questions."},
            status=status.HTTP_403_FORBIDDEN,
        )

    qa = get_object_or_404(ProjectQA, pk=pk, project=project)
    answer_text = request.data.get("answer") or request.data.get("body")
    if not answer_text or not str(answer_text).strip():
        return Response(
            {"detail": "Answer text cannot be blank."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from django.utils import timezone

    qa.answer = str(answer_text).strip()
    qa.answered_at = timezone.now()
    qa.save(update_fields=["answer", "answered_at"])

    return Response(ProjectQASerializer(qa).data)
