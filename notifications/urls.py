from django.urls import path

from .views import (
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    notification_unread_count,
    telegram_webhook,
)

urlpatterns = [
    path("webhook/<str:secret>/", telegram_webhook, name="telegram_webhook"),
    path("", notification_list, name="notification_list"),
    path("read/", notification_mark_all_read, name="notification_mark_all_read"),
    path("<int:pk>/read/", notification_mark_read, name="notification_mark_read"),
    path("unread-count/", notification_unread_count, name="notification_unread_count"),
]
