import os
from celery import Celery

# Set default settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cooplink.settings")

app = Celery("cooplink")

# Configure namespace to load Celery settings from Django settings with CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks in all apps
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
