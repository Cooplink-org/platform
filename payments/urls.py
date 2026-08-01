from django.urls import path

from . import views

urlpatterns = [
    path("mirpay/webhook/success/", views.mirpay_webhook_success, name="mirpay_webhook_success"),
    path("mirpay/webhook/fail/", views.mirpay_webhook_fail, name="mirpay_webhook_fail"),
    path("mirpay/balance/", views.mirpay_balance, name="mirpay_balance"),
    path("mirpay/verify/", views.mirpay_verify_payment, name="mirpay_verify_payment"),
]
