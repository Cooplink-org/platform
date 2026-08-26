from django.urls import path

from . import views

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("entries/", views.entry_create, name="leaderboard_entry_create"),
    path("entries/<int:pk>/pay/", views.entry_pay, name="leaderboard_entry_pay"),
    path("entries/<int:pk>/click/", views.entry_click, name="leaderboard_entry_click"),
    path("verify/", views.verify, name="leaderboard_verify"),
]
