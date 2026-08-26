from django.urls import path

from . import views

urlpatterns = [
    path("providers/", views.payment_providers, name="payment_providers"),
    path("success/", views.payment_success_redirect, name="payment_success_redirect"),
    path("cancel/", views.payment_cancel_redirect, name="payment_cancel_redirect"),
    path("mirpay/success/", views.mirpay_success_redirect, name="mirpay_success_redirect"),
    path("mirpay/cancel/", views.mirpay_cancel_redirect, name="mirpay_cancel_redirect"),
    path("mirpay/webhook/", views.mirpay_webhook, name="mirpay_webhook"),
    path("mirpay/webhook/success/", views.mirpay_webhook_success, name="mirpay_webhook_success"),
    path("mirpay/webhook/fail/", views.mirpay_webhook_fail, name="mirpay_webhook_fail"),
    path("mirpay/balance/", views.mirpay_balance, name="mirpay_balance"),
    path("mirpay/verify/", views.mirpay_verify_payment, name="mirpay_verify_payment"),
    path("verify/", views.payment_verify, name="payment_verify"),
    path("inpay/success/", views.inpay_success_redirect, name="inpay_success_redirect"),
    path("inpay/cancel/", views.inpay_cancel_redirect, name="inpay_cancel_redirect"),
    path("inpay/webhook/", views.inpay_webhook, name="inpay_webhook"),
    path("inpay/verify/", views.inpay_verify_payment, name="inpay_verify_payment"),
]
