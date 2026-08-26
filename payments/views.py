import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.db import transaction as db_transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from leaderboard.models import LeaderboardEntry
from leaderboard.views import confirm_entry
from orders.models import Order, Transaction

from .inpay import InPayClient, InPayError
from .mirpay import MirPayClient, is_failed_status, is_success_status
from .models import PaymentProviderConfig, WebhookLog

log = logging.getLogger(__name__)
mirpay = MirPayClient()


def _get_callback_secret():
    """Return the MirPay callback secret from DB config or env fallback."""
    config = PaymentProviderConfig.objects.filter(
        provider=PaymentProviderConfig.Provider.MIRPAY, enabled=True
    ).first()
    if config and config.callback_secret:
        return config.callback_secret.encode()
    secret = settings.MIRPAY_CALLBACK_SECRET
    return secret.encode() if secret else b""


def _verify_callback_signature(request):
    """Verify MirPay HMAC-SHA256 callback signature.

    Returns (is_valid, status_code, message).
    """
    secret = _get_callback_secret()
    if not secret:
        log.warning("MirPay callback secret not configured — skipping signature verification")
        return True, 200, "ok"

    timestamp = request.headers.get("X-MirPay-Timestamp", "")
    signature = request.headers.get("X-MirPay-Signature", "")

    if not timestamp or not signature:
        log.warning("MirPay callback missing signature headers")
        return False, 403, "bad signature"

    # Reject stale callbacks (> 300s) as per MirPay v2 specification
    try:
        if abs(time.time() - int(timestamp)) > 300:
            log.warning("MirPay callback timestamp too old: %s", timestamp)
            return False, 408, "stale"
    except (ValueError, TypeError):
        log.warning("MirPay callback invalid timestamp: %s", timestamp)
        return False, 408, "stale"

    raw_body = request.body.decode("utf-8", errors="replace")
    expected = hmac.new(secret, f"{timestamp}.{raw_body}".encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        log.warning("MirPay callback signature mismatch")
        return False, 403, "bad signature"

    return True, 200, "ok"


def _frontend_redirect(path, request):
    """Redirect the buyer's browser to a frontend page, preserving query string."""
    base = settings.FRONTEND_URL.rstrip("/")
    query = request.GET.urlencode()
    url = f"{base}{path}" + (f"?{query}" if query else "")
    return HttpResponseRedirect(url)


def mirpay_success_redirect(request):
    """Same-domain success landing MirPay can redirect to; forwards to the SPA."""
    log.info("MirPay success redirect query: %s", dict(request.GET))
    return _frontend_redirect("/payment/success", request)


def mirpay_cancel_redirect(request):
    """Same-domain cancel landing MirPay can redirect to; forwards to the SPA."""
    return _frontend_redirect("/payment/cancel", request)


def inpay_success_redirect(request):
    """Same-domain success landing inPAY can redirect to; forwards to the SPA."""
    log.info("inPAY success redirect query: %s", dict(request.GET))
    return _frontend_redirect("/payment/success", request)


def inpay_cancel_redirect(request):
    """Same-domain cancel landing inPAY can redirect to; forwards to the SPA."""
    return _frontend_redirect("/payment/cancel", request)


def payment_success_redirect(request):
    """Generic success landing payment providers can redirect to; forwards to the SPA."""
    log.info("Payment success redirect query: %s", dict(request.GET))
    return _frontend_redirect("/payment/success", request)


def payment_cancel_redirect(request):
    """Generic cancel landing payment providers can redirect to; forwards to the SPA."""
    return _frontend_redirect("/payment/cancel", request)


@csrf_exempt
@require_POST
def mirpay_webhook(request):
    """Unified MirPay webhook — MirPay only allows one webhook URL.

    Independently verifies the payment status with MirPay and acts accordingly:
    - If verified as paid  → confirm the order.
    - If verified as failed → mark the order as failed.
    - If status is unclear  → keep pending (will be resolved via frontend polling).
    """
    return _handle_webhook(request, endpoint="webhook")


@csrf_exempt
@require_POST
def mirpay_webhook_success(request):
    return _handle_webhook(request, endpoint="success")


@csrf_exempt
@require_POST
def mirpay_webhook_fail(request):
    return _handle_webhook(request, endpoint="fail")


def _handle_webhook(request, endpoint):
    # Verify MirPay callback signature first
    valid, status_code, msg = _verify_callback_signature(request)
    if not valid:
        return HttpResponse(msg, status=status_code)

    raw_body = request.body.decode("utf-8", errors="replace")
    parsed = _parse_form_body(raw_body)

    log.info("MirPay webhook [%s] raw: %s", endpoint, raw_body)

    payid = parsed.get("payid") or parsed.get("PayId")
    comment = parsed.get("comment") or parsed.get("Comment")
    _ = parsed.get("chek")
    _ = parsed.get("fiskal")
    _ = parsed.get("sana")

    wh_log = WebhookLog.objects.create(
        endpoint=endpoint,
        raw_body=raw_body,
    )

    if not payid:
        log.warning("Webhook [%s] missing payid — %s", endpoint, raw_body)
        wh_log.verification_response = {"error": "missing payid"}
        wh_log.save(update_fields=["verification_response"])
        return HttpResponse("ok")

    # Never trust the webhook alone — independently verify with MirPay
    try:
        verification = mirpay.check_status(payid)
    except Exception as exc:
        log.error("MirPay check_status failed for payid=%s: %s", payid, exc)
        wh_log.verification_response = {"error": str(exc)}
        wh_log.save(update_fields=["verification_response"])
        return HttpResponse("verification failed", status=502)

    wh_log.verification_response = verification
    wh_log.save(update_fields=["verification_response"])

    # Match the order via payment_ref (payid) first, then comment field
    comment_clean = (comment or "").strip()
    order = _resolve_order(raw_body, comment_clean, verification, payid=payid)

    if order:
        wh_log.matched_order = order
        wh_log.save(update_fields=["matched_order"])

    if not order:
        return HttpResponse("ok")

    # Idempotency: ignore if not pending_payment
    if order.status != Order.Status.PENDING_PAYMENT:
        log.info(
            "Order %s status is %s — ignoring webhook [%s]",
            order.id,
            order.status,
            endpoint,
        )
        return HttpResponse("ok")

    # Verify summa matches
    verified_summa = verification.get("summa") or verification.get("Summa")
    verified_status_ok = is_success_status(verification)

    if verified_summa:
        try:
            if float(verified_summa) != float(order.price_at_purchase):
                log.warning(
                    "Summa mismatch for order %s: webhook summa=%s, expected=%s",
                    order.id,
                    verified_summa,
                    order.price_at_purchase,
                )
        except (ValueError, TypeError):
            log.error("Could not parse verified_summa: %s", verified_summa)

    # Unified webhook — trust the verification result
    if endpoint == "webhook":
        if verified_status_ok:
            _confirm_payment(order)
            log.info("Order %s marked paid via unified webhook", order.id)
            return HttpResponse("ok")
        verified_failed = is_failed_status(verification)
        if verified_failed:
            order.status = Order.Status.FAILED
            order.save(update_fields=["status"])
            log.warning("Order %s marked failed via unified webhook", order.id)
            return HttpResponse("ok")
        log.warning(
            "Unified webhook for order %s but verification unclear (%s) — keeping pending.",
            order.id,
            verification.get("status"),
        )
        return HttpResponse("ok")

    if endpoint == "success" and verified_status_ok:
        _confirm_payment(order)
        log.info("Order %s marked paid via webhook [success]", order.id)
        return HttpResponse("ok")

    # Fail webhook and verification explicitly confirms failure
    if endpoint == "fail" and not verified_status_ok:
        verified_failed = is_failed_status(verification)
        if verified_failed:
            order.status = Order.Status.FAILED
            order.save(update_fields=["status"])
            log.warning("Order %s marked failed via webhook [fail]", order.id)
            return HttpResponse("ok")
        # Verification is unclear (e.g. "processing") — don't mark failed yet
        log.warning(
            "Fail webhook for order %s but verification unclear (%s) — keeping pending.",
            order.id,
            verification.get("status"),
        )
        return HttpResponse("ok")

    # Fail webhook but verification says it's OK — trust the API, not the webhook
    if endpoint == "fail" and verified_status_ok:
        _confirm_payment(order)
        log.info("Order %s marked paid despite fail webhook (verification OK)", order.id)
        return HttpResponse("ok")

    # Success/ambiguous webhook but verification didn't confirm — keep pending
    log.warning(
        "Webhook [%s] for order %s but verification didn't confirm — keeping pending. "
        "verification=%s",
        endpoint,
        order.id,
        json.dumps(verification, ensure_ascii=False),
    )
    return HttpResponse("ok")


def _parse_form_body(raw_body):
    """Parse form-encoded body into a dict."""
    from urllib.parse import parse_qs

    parsed = parse_qs(raw_body)
    return {k: v[0] for k, v in parsed.items() if v}


def _resolve_order(_raw_body, comment, verification, payid=None):
    """Try to find the Order by payment_ref first, then webhook comment or verification."""
    if payid:
        order = Order.objects.filter(payment_ref=str(payid)).first()
        if order:
            return order

    if comment:
        if "Buyurtma ID:" in comment:
            try:
                order_id = comment.split("Buyurtma ID:")[1].strip().split()[0]
                order = Order.objects.filter(id=order_id).first()
                if order:
                    return order
            except (IndexError, ValueError):
                pass
        elif "Buyurtma #" in comment:
            try:
                order_id = comment.split("Buyurtma #")[1].strip().split()[0]
                order = Order.objects.filter(id=order_id).first()
                if order:
                    return order
            except (IndexError, ValueError):
                pass

    # Fallback: try info_pay, order_info, or comment from verification
    info_pay = (
        verification.get("info_pay")
        or verification.get("comment")
        or verification.get("order_info")
        or ""
    )
    if "Buyurtma ID:" in str(info_pay):
        try:
            order_id = str(info_pay).split("Buyurtma ID:")[1].strip().split()[0]
            order = Order.objects.filter(id=order_id).first()
            if order:
                return order
        except (IndexError, ValueError):
            pass
    elif "Buyurtma #" in str(info_pay):
        try:
            order_id = str(info_pay).split("Buyurtma #")[1].strip().split()[0]
            order = Order.objects.filter(id=order_id).first()
            if order:
                return order
        except (IndexError, ValueError):
            pass

    return None


@db_transaction.atomic
def _confirm_payment(order):
    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.save(update_fields=["status", "paid_at"])

    Transaction.objects.create(
        user=order.seller,
        order=order,
        type=Transaction.Type.SALE_EARNING,
        amount=order.seller_earning_amount,
    )
    Transaction.objects.create(
        user=order.seller,
        order=order,
        type=Transaction.Type.PLATFORM_FEE,
        amount=order.platform_fee_amount,
    )

    # Notify seller of the sale
    from notifications.tasks import notify_user_task

    notify_user_task.delay(
        order.seller_id,
        "sale_made",
        {"title": order.project.title, "amount": f"{order.seller_earning_amount:,.2f}"},
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def mirpay_balance(_request):
    """GET /api/payments/mirpay/balance/ — staff-only, returns current MirPay balance."""
    try:
        balance = mirpay.get_balance()
        return Response(balance)
    except Exception as exc:
        log.error("MirPay balance check failed: %s", exc)
        return Response({"error": str(exc)}, status=502)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mirpay_verify_payment(request):
    """
    POST /api/payments/mirpay/verify/
    Called from the frontend success page after MirPay redirects.
    Independently verifies payment status with MirPay and confirms the order.
    """
    payid = request.data.get("payid")
    if not payid:
        return Response({"error": "payid is required"}, status=400)
    return _do_mirpay_verify(payid)


def _do_mirpay_verify(payid):
    try:
        verification = mirpay.check_status(payid)
    except Exception as exc:
        log.error("MirPay check_status failed for payid=%s: %s", payid, exc)
        return Response({"error": str(exc)}, status=502)

    log.info(
        "verify_payment payid=%s verification=%s",
        payid,
        json.dumps(verification, ensure_ascii=False),
    )

    verified_status_ok = is_success_status(verification)

    order = Order.objects.filter(payment_ref=payid).first()

    if order and order.status == Order.Status.PAID:
        return Response({"status": "paid", "order_id": order.id})

    if order and verified_status_ok:
        if order.status in (Order.Status.PENDING_PAYMENT, Order.Status.FAILED):
            _confirm_payment(order)
            log.info(
                "Order %s marked paid via verify endpoint (was %s)",
                order.id,
                order.status,
            )
            return Response({"status": "paid", "order_id": order.id})
        return Response({"status": "paid", "order_id": order.id})

    # Don't mark as failed — just report the current state
    if order:
        return Response({"status": order.status, "order_id": order.id})

    return Response({"status": "unknown", "detail": "Order not found or status unclear"})


# ── inPAY ────────────────────────────────────────────────────────────────────


@csrf_exempt
@require_POST
def inpay_webhook(request):
    """POST /api/payments/inpay/webhook/ — inPAY payment notification.

    inPAY sends a JSON payload with {amount, status, order_id, transaction_id,
    created_at}. We match the order via payment_ref (inPAY order_id) and
    independently verify the status before confirming.
    """
    raw_body = request.body.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        log.warning("inPAY webhook — invalid JSON: %s", raw_body[:500])
        return HttpResponse("OK", status=200)

    log.info("inPAY webhook raw: %s", raw_body)

    wh_log = WebhookLog.objects.create(endpoint="inpay", raw_body=raw_body)

    order_id = data.get("order_id")
    if not order_id:
        log.warning("inPAY webhook missing order_id — %s", raw_body)
        wh_log.verification_response = {"error": "missing order_id"}
        wh_log.save(update_fields=["verification_response"])
        return HttpResponse("OK", status=200)

    # Match order via payment_ref (inPAY order_id stored at creation time)
    order = Order.objects.filter(payment_ref=order_id, provider=Order.Provider.INPAY).first()

    if not order:
        # Not a marketplace order — maybe a Crack It leaderboard entry
        entry = LeaderboardEntry.objects.filter(payment_ref=order_id).first()
        if entry:
            wh_log.verification_response = data
            wh_log.save(update_fields=["verification_response"])
            return _handle_leaderboard_entry_webhook(entry, order_id, wh_log)
        log.warning("inPAY webhook — no matching order for order_id=%s", order_id)
        wh_log.verification_response = data
        wh_log.save(update_fields=["verification_response"])
        return HttpResponse("OK", status=200)

    wh_log.matched_order = order
    wh_log.verification_response = data
    wh_log.save(update_fields=["matched_order", "verification_response"])

    # Idempotency: ignore if not pending_payment
    if order.status != Order.Status.PENDING_PAYMENT:
        log.info(
            "Order %s status is %s — ignoring inPAY webhook",
            order.id,
            order.status,
        )
        return HttpResponse("OK", status=200)

    # Independently verify with inPAY (never trust the webhook alone)
    try:
        client = InPayClient()
        verification = client.check_status(order_id)
    except Exception as exc:
        log.error("inPAY check_status failed for order_id=%s: %s", order_id, exc)
        wh_log.verification_response = {"error": str(exc), "webhook": data}
        wh_log.save(update_fields=["verification_response"])
        # Return non-200 so inPAY retries the webhook
        return HttpResponse("verification failed", status=502)

    wh_log.verification_response = verification
    wh_log.save(update_fields=["verification_response"])

    verified_status = str(verification.get("status", "")).strip().lower()

    # Verify amount matches
    verified_amount = verification.get("amount")
    if verified_amount:
        try:
            if float(verified_amount) != float(order.price_at_purchase):
                log.warning(
                    "Amount mismatch for order %s: inPAY amount=%s, expected=%s",
                    order.id,
                    verified_amount,
                    order.price_at_purchase,
                )
        except (ValueError, TypeError):
            log.error("Could not parse verified amount: %s", verified_amount)

    if verified_status == "success":
        _confirm_payment(order)
        log.info("Order %s marked paid via inPAY webhook", order.id)
        return HttpResponse("OK", status=200)

    if verified_status in ("failed", "cancelled"):
        order.status = Order.Status.FAILED
        order.save(update_fields=["status"])
        log.warning(
            "Order %s marked failed via inPAY webhook (status=%s)",
            order.id,
            verified_status,
        )
        return HttpResponse("OK", status=200)

    # Status is "pending" or unclear — keep the order pending
    log.warning(
        "inPAY webhook for order %s but verification status is '%s' — keeping pending",
        order.id,
        verified_status,
    )
    return HttpResponse("OK", status=200)


def _handle_leaderboard_entry_webhook(entry, order_id, wh_log):
    """Verify an inPAY payment for a Crack It leaderboard entry and confirm it.

    Mirrors the Order webhook logic: never trust the webhook alone, always
    re-check the status with inPAY before confirming or failing the entry.
    """
    if entry.status == LeaderboardEntry.Status.PAID:
        log.info("Leaderboard entry %s already paid — ignoring inPAY webhook", entry.id)
        return HttpResponse("OK", status=200)

    try:
        client = InPayClient()
        verification = client.check_status(order_id)
    except Exception as exc:
        log.error("inPAY check_status failed for leaderboard ref=%s: %s", order_id, exc)
        wh_log.verification_response = {"error": str(exc), "webhook": wh_log.verification_response}
        wh_log.save(update_fields=["verification_response"])
        return HttpResponse("verification failed", status=502)

    wh_log.verification_response = verification
    wh_log.save(update_fields=["verification_response"])

    verified_status = str(verification.get("status", "")).strip().lower()
    verified_amount = verification.get("amount")
    if verified_amount:
        try:
            if float(verified_amount) != float(entry.amount_uzs):
                log.warning(
                    "Amount mismatch for leaderboard entry %s: inPAY amount=%s, expected=%s",
                    entry.id,
                    verified_amount,
                    entry.amount_uzs,
                )
        except (ValueError, TypeError):
            log.error("Could not parse verified amount: %s", verified_amount)

    if verified_status == "success":
        confirm_entry(entry)
        log.info("Leaderboard entry %s marked paid via inPAY webhook", entry.id)
        return HttpResponse("OK", status=200)

    if verified_status in ("failed", "cancelled"):
        entry.status = LeaderboardEntry.Status.FAILED
        entry.save(update_fields=["status"])
        log.warning(
            "Leaderboard entry %s marked failed via inPAY webhook (status=%s)",
            entry.id,
            verified_status,
        )
        return HttpResponse("OK", status=200)

    log.warning(
        "inPAY webhook for leaderboard entry %s but verification status is '%s' — keeping pending",
        entry.id,
        verified_status,
    )
    return HttpResponse("OK", status=200)


@api_view(["GET"])
@permission_classes([AllowAny])
def payment_providers(_request):
    """GET /api/payments/providers/
    Returns the enabled payment providers so the frontend can offer a choice.
    Public — no auth needed.
    """
    from .models import PaymentProviderConfig

    configs = PaymentProviderConfig.objects.filter(enabled=True).order_by("-is_default")
    providers = []
    for cfg in configs:
        providers.append(
            {
                "provider": cfg.provider,
                "display_name": cfg.get_provider_display(),
                "is_default": cfg.is_default,
            }
        )
    return Response({"providers": providers})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def inpay_verify_payment(request):
    """POST /api/payments/inpay/verify/

    Called from the frontend after the customer returns from inPAY checkout.
    Independently verifies payment status with inPAY and confirms the order.
    """
    order_id = request.data.get("order_id")
    if not order_id:
        return Response({"error": "order_id is required"}, status=400)
    return _do_inpay_verify(order_id)


def _do_inpay_verify(order_id):
    try:
        client = InPayClient()
        verification = client.check_status(order_id)
    except InPayError as exc:
        return Response({"error": str(exc)}, status=503)
    except Exception as exc:
        log.error("inPAY check_status failed for order_id=%s: %s", order_id, exc)
        return Response({"error": str(exc)}, status=502)

    log.info(
        "inpay_verify order_id=%s verification=%s",
        order_id,
        json.dumps(verification, ensure_ascii=False),
    )

    verified_status = str(verification.get("status", "")).strip().lower()
    order = Order.objects.filter(payment_ref=order_id, provider=Order.Provider.INPAY).first()

    if order and order.status == Order.Status.PAID:
        return Response({"status": "paid", "order_id": order.id})

    if order and verified_status == "success":
        if order.status in (Order.Status.PENDING_PAYMENT, Order.Status.FAILED):
            _confirm_payment(order)
            log.info(
                "Order %s marked paid via inPAY verify endpoint (was %s)",
                order.id,
                order.status,
            )
            return Response({"status": "paid", "order_id": order.id})
        return Response({"status": "paid", "order_id": order.id})

    if order:
        return Response({"status": order.status, "order_id": order.id})

    return Response({"status": "unknown", "detail": "Order not found or status unclear"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_verify(request):
    """POST /api/payments/verify/

    Unified, provider-agnostic payment verification endpoint.
    Automatically detects whether the order was created via inPAY or MirPay
    and verifies payment status with the appropriate gateway.
    """
    ref = (
        request.data.get("payid")
        or request.data.get("order_id")
        or request.data.get("payment_id")
        or request.data.get("PayId")
    )
    if not ref:
        return Response({"error": "payid or order_id is required"}, status=400)

    order = Order.objects.filter(payment_ref=str(ref)).first()
    if order and order.provider == Order.Provider.INPAY:
        return _do_inpay_verify(str(ref))

    return _do_mirpay_verify(str(ref))
