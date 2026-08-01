from django.urls import path

from . import views

urlpatterns = [
    path("github/login/", views.github_login, name="github_login"),
    path("github/callback/", views.github_callback, name="github_callback"),
    path("github/connect-repos/", views.github_connect_repos, name="github_connect_repos"),
    path("github/repos/", views.github_my_repos, name="github_my_repos"),
    path("me/", views.current_user, name="current_user"),
    path("onboarding/", views.onboarding_submit, name="onboarding_submit"),
    path("phone/link/", views.phone_link, name="phone_link"),
    path("phone/verify/", views.phone_verify, name="phone_verify"),
    path("phone/status/", views.phone_status, name="phone_status"),
]
