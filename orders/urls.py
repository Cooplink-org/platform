from django.urls import path
from . import views

urlpatterns = [
    path("", views.order_create, name="order_create"),
    path("<int:pk>/download/", views.order_download, name="order_download"),
]
