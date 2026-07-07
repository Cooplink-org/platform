from django.urls import path
from . import views

urlpatterns = [
    path("request/", views.payout_request_create, name="payout_request_create"),
    path("mine/", views.payout_list_mine, name="payout_list_mine"),
]
