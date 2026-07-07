from django.http import JsonResponse, HttpResponse
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
import os

@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """
    Simple health check endpoint returning status code 200 and details.
    """
    return JsonResponse({"status": "healthy"})

def serve_playground(request):
    """Serve the dev playground index.html from the project root."""
    path = os.path.join(settings.BASE_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HttpResponse(f.read())
