import requests

from django.db import connection
from django.db.models import F, Q
from django.contrib.postgres.search import SearchQuery, SearchVector
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework import generics

from accounts.utils import decrypt_token
from .models import Project, Category
from .serializers import PublicProjectSerializer, ProjectSerializer, CategorySerializer


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_seller(user):
    """Return None if user is a seller, or a 403 Response otherwise."""
    if not user.is_seller or not user.github_token_encrypted:
        return Response(
            {"detail": "You must complete the seller GitHub authorization first. "
                       "Visit /api/auth/github/connect-repos/ to enable seller features."},
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
        qs = Project.objects.filter(seller=request.user)
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
    PATCH  /api/listings/projects/{id}/  — update fields (draft/rejected only).
    DELETE /api/listings/projects/{id}/  — delete a project (draft/rejected only).

    Direct edits are blocked once a project is pending_review or published.
    Reason: we snapshot the repo at publish time, so allowing silent edits would
    let sellers change what buyers already paid for. A new version cycle is the
    only approved path.
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

    if not project.is_editable:
        return Response(
            {"detail": f"Project is {project.get_status_display()} and cannot be directly edited. "
                       "Submit a new version instead."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if request.method == "PATCH":
        serializer = ProjectSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectSerializer(project).data)

    # DELETE
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
            {"detail": f"Only draft projects can be submitted. Current status: {project.get_status_display()}."},
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

    # Phase 3 will trigger the Celery snapshot task here.
    # For now we simply transition the status.

    return Response(ProjectSerializer(project).data)


# ── public catalog ───────────────────────────────────────────────────────────

class ProjectPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

@api_view(["GET"])
@permission_classes([AllowAny])
def category_list(request):
    """GET /api/listings/categories/ — returns all categories."""
    cats = Category.objects.all().order_by("name")
    serializer = CategorySerializer(cats, many=True)
    return Response(serializer.data)

class PublicProjectList(generics.ListAPIView):
    """
    GET /api/listings/
    Published projects only, paginated, filterable by category/tags/price range/tech_stack,
    sortable by newest/price/popularity.
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
    serializer_class = PublicProjectSerializer
    permission_classes = [AllowAny]
    pagination_class = ProjectPagination
    filter_backends = [OrderingFilter]
    ordering = ["-created_at"]
    ordering_fields = ["created_at", "price", "view_count"]

    def get_queryset(self):
        qs = Project.objects.filter(status=Project.Status.PUBLISHED).select_related("category", "seller")

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

        q = self.request.query_params.get("q")
        if q:
            if connection.vendor == "postgresql":
                search_vector = SearchVector("title", weight="A") + SearchVector("description", weight="B")
                qs = qs.annotate(search=search_vector).filter(search=SearchQuery(q))
            else:
                qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))

        return qs


@api_view(["GET"])
@permission_classes([AllowAny])
def project_detail_public(request, slug):
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
