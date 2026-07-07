from django.urls import path
from . import views

urlpatterns = [
    # Public catalog
    path("categories/", views.category_list, name="category_list"),
    path("", views.PublicProjectList.as_view(), name="public_project_list"),
    # Seller endpoints (must come before the catch-all slug route)
    path("my-repos/", views.my_repos, name="my_repos"),
    path("projects/", views.project_list_create, name="project_list_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/submit/", views.project_submit, name="project_submit"),
    # Public detail — catch-all slug route last
    path("<slug:slug>/", views.project_detail_public, name="project_detail_public"),
]
