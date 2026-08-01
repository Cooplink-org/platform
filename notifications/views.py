"""
Django view that receives Telegram webhook updates.

Uses requests library directly to call Telegram's Bot API instead of
aiogram's async machinery, avoiding event loop issues on Windows.
"""

import contextlib
import json
import logging
import os
import secrets
import uuid

import requests as http_requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Rate limits
CODE_REQUEST_RATE_LIMIT = 3
CODE_REQUEST_RATE_WINDOW = 15 * 60


def _get_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API using requests (sync)."""
    token = _get_bot_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return False
    try:
        resp = http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.ok:
            return True
        logger.error("Telegram sendMessage failed: %s", resp.text)
        return False
    except http_requests.RequestException as exc:
        logger.error("Telegram sendMessage error: %s", exc)
        return False


def _send_phone_keyboard(chat_id: str, text: str) -> bool:
    """Send a message with phone request keyboard."""
    token = _get_bot_token()
    if not token:
        return False
    try:
        resp = http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "keyboard": [
                        [{"text": "\U0001f4de Share phone number", "request_contact": True}]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
            timeout=10,
        )
        return resp.ok
    except http_requests.RequestException as exc:
        logger.error("Telegram sendPhoneKeyboard error: %s", exc)
        return False


def _remove_keyboard(chat_id: str) -> None:
    """Remove the reply keyboard."""
    token = _get_bot_token()
    if not token:
        return
    with contextlib.suppress(http_requests.RequestException):
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "\u2705 Phone number received!\n\n"
                "Your verification code has been generated and will be sent to you shortly.\n"
                "The code expires in <b>5 minutes</b>.\n\n"
                "Enter the code on the Cooplink page to complete verification.",
                "parse_mode": "HTML",
                "reply_markup": {"remove_keyboard": True},
            },
            timeout=10,
        )


@csrf_exempt
@require_POST
def telegram_webhook(request, secret):
    """
    POST /api/telegram/webhook/<secret>/

    Handles Telegram webhook updates synchronously using requests library.
    No asyncio needed — avoids event loop issues on Windows.
    """
    # Verify webhook secret
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected_secret:
        logger.error("TELEGRAM_WEBHOOK_SECRET is not configured")
        return HttpResponseForbidden("Webhook secret not configured")

    if secret != expected_secret:
        return HttpResponseForbidden("Invalid webhook secret")

    # Verify Telegram header if configured
    expected_header = os.environ.get("TELEGRAM_BOT_API_SECRET_TOKEN", "")
    if expected_header:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != expected_header:
            return HttpResponseForbidden("Invalid Telegram secret token header")

    # Parse update
    try:
        update = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponseBadRequest("Invalid JSON")

    logger.info("Received Telegram update: %s", update.get("update_id", "unknown"))

    try:
        _handle_update(update)
    except Exception as exc:
        logger.exception("Error handling Telegram update: %s", exc)

    return HttpResponse(status=200)


def _handle_update(update: dict):
    """Process a Telegram update synchronously."""
    message = update.get("message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    from_user = message.get("from", {})
    text = message.get("text", "")
    entities = message.get("entities", [])
    contact = message.get("contact")

    # Check for /start command
    is_start_command = False
    start_payload = None
    for entity in entities:
        if entity.get("type") == "bot_command" and text.startswith("/start"):
            is_start_command = True
            # Extract payload after /start
            start_payload = text[7:].strip() if len(text) > 7 else None
            break

    if is_start_command:
        _handle_start(chat_id, from_user, start_payload)
    elif contact:
        _handle_contact(chat_id, from_user, contact)
    else:
        _handle_fallback(chat_id)


def _handle_start(chat_id: str, _from_user: dict, payload: str | None):
    """Handle /start command with optional linking token payload."""
    from notifications.models import TelegramLinkingToken

    if not payload:
        _send_telegram_message(
            chat_id,
            "\u26a0\ufe0f This bot is used for phone verification on Cooplink.\n\n"
            "To get started, go to your Cooplink settings and click "
            '"<b>Verify phone via Telegram</b>" — it will open this bot '
            "with a special link.",
        )
        return

    # Validate linking token
    try:
        token_uuid = uuid.UUID(payload)
        linking_token = TelegramLinkingToken.objects.select_related("user").get(token=token_uuid)
    except (ValueError, TelegramLinkingToken.DoesNotExist):
        _send_telegram_message(
            chat_id,
            "\u274c This verification link is <b>invalid or expired</b>.\n\n"
            "Go back to your Cooplink settings and request a new link.",
        )
        return

    if not linking_token.is_valid:
        _send_telegram_message(
            chat_id,
            "\u274c This verification link is <b>invalid or expired</b>.\n\n"
            "Go back to your Cooplink settings and request a new link.",
        )
        return

    # Consume the token
    linking_token.telegram_chat_id = chat_id
    linking_token.consumed = True
    linking_token.consumed_at = timezone.now()
    linking_token.save(update_fields=["telegram_chat_id", "consumed", "consumed_at"])

    user = linking_token.user
    user.telegram_chat_id = chat_id
    user.save(update_fields=["telegram_chat_id"])

    name = user.username or user.full_legal_name or "there"
    _send_phone_keyboard(
        chat_id,
        f"\U0001f44b Hi <b>{name}</b>!\n\n"
        "Your Cooplink account has been linked.\n\n"
        "To verify your phone number, tap the button below to share it with Telegram.\n"
        "Your phone number will be used <b>only</b> for verification purposes.",
    )


def _handle_contact(chat_id: str, from_user: dict, contact: dict):
    """Handle incoming contact (phone number sharing)."""
    from notifications.models import PhoneVerificationCode

    user_model = get_user_model()

    # Security: verify contact belongs to sender
    contact_user_id = contact.get("user_id")
    from_user_id = from_user.get("id")
    if contact_user_id != from_user_id:
        _send_phone_keyboard(
            chat_id,
            "\u26a0\ufe0f The phone number you shared doesn't belong to your Telegram account.\n\n"
            "For security, you can only verify <b>your own</b> phone number.\n"
            "Please tap the button below again to share your actual phone number.",
        )
        return

    # Look up user
    try:
        user = user_model.objects.get(telegram_chat_id=chat_id)
    except user_model.DoesNotExist:
        _send_telegram_message(
            chat_id,
            "\u274c Your Telegram account is not linked to any Cooplink account.\n\n"
            'Go to your Cooplink settings and click "<b>Verify phone via Telegram</b>" first.',
        )
        return

    # Rate limit check
    cache_key = f"phone_code_requests:{user.id}"
    try:
        current = cache.get(cache_key, 0)
        if current >= CODE_REQUEST_RATE_LIMIT:
            _send_telegram_message(
                chat_id,
                "\u23f3 You've requested too many codes recently.\n\n"
                "Please wait a few minutes before requesting a new one.",
            )
            return
        cache.set(cache_key, current + 1, CODE_REQUEST_RATE_WINDOW)
    except Exception:
        # Redis unavailable — skip rate limiting rather than crashing the webhook.
        pass

    # Normalize phone number
    phone_number = contact.get("phone_number", "")
    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    # Check if already verified on another account
    if (
        user_model.objects.filter(phone_number=phone_number, phone_verified=True)
        .exclude(pk=user.pk)
        .exists()
    ):
        _send_telegram_message(
            chat_id,
            "\u274c This phone number is already verified on another Cooplink account.\n\n"
            "Each phone number can only be linked to one account.",
        )
        return

    # Generate code
    code = f"{secrets.randbelow(1_000_000):06d}"
    PhoneVerificationCode.objects.create(
        user=user,
        code=code,
        phone_number=phone_number,
        telegram_chat_id=chat_id,
        expires_at=timezone.now() + timezone.timedelta(minutes=5),
    )

    # Send confirmation and remove keyboard
    _remove_keyboard(chat_id)

    # Dispatch Celery task to send the code
    from notifications.tasks import send_verification_code_task

    send_verification_code_task.delay(chat_id, code)


def _handle_fallback(chat_id: str):
    """Handle any other message."""
    _send_telegram_message(
        chat_id,
        "\U0001f916 This bot is used for phone verification on Cooplink.\n\n"
        "To verify your phone number, go to your Cooplink settings and click "
        '"<b>Verify phone via Telegram</b>".',
    )


# ── In-platform notification inbox API ─────────────────────────────────────────


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_list(request):
    """
    GET /api/notifications/
    Paginated list of notifications for the current user, newest first.
    """
    from .models import Notification

    page_size = int(request.query_params.get("page_size", 20))
    page_size = max(1, min(page_size, 100))
    page = int(request.query_params.get("page", 1))
    page = max(1, page)

    qs = Notification.objects.filter(recipient=request.user).order_by("-created_at")
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = qs[start:end]

    from .serializers import NotificationSerializer

    serializer = NotificationSerializer(items, many=True)
    return Response(
        {
            "count": total,
            "next": f"?page={page + 1}&page_size={page_size}" if end < total else None,
            "previous": f"?page={page - 1}&page_size={page_size}" if page > 1 else None,
            "results": serializer.data,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_read(request, pk):
    """
    POST /api/notifications/{id}/read/
    Mark a single notification as read.
    """
    from .models import Notification

    try:
        notif = Notification.objects.get(pk=pk, recipient=request.user)
    except Notification.DoesNotExist:
        return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
    notif.is_read = True
    notif.save(update_fields=["is_read"])
    return Response({"detail": "ok"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_all_read(request):
    """
    POST /api/notifications/read/
    Mark all unread notifications for the current user as read.
    """
    from .models import Notification

    updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
        is_read=True
    )
    return Response({"detail": f"{updated} notifications marked as read."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_unread_count(request):
    """
    GET /api/notifications/unread-count/
    Returns { "count": N } for the red badge.
    """
    from .models import Notification

    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return Response({"count": count})
