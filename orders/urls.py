from django.urls import path

from . import views

urlpatterns = [
    path("", views.order_create, name="order_create"),
    path("my-purchases/", views.my_purchases, name="my_purchases"),
    path("<int:pk>/status/", views.order_status, name="order_status"),
    path("<int:pk>/download/", views.order_download, name="order_download"),
]
