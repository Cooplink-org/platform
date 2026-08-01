from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(_request):
    """
    Simple health check endpoint returning status code 200 and details.
    """
    return JsonResponse({"status": "healthy"})


def serve_playground(_request):
    """Serve the dev playground index.html from the project root. Only in DEBUG mode."""
    if not settings.DEBUG:
        raise Http404
    path = Path(settings.BASE_DIR) / "index.html"
    return HttpResponse(path.read_text(encoding="utf-8"))
