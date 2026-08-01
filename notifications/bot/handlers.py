"""
aiogram message handlers for the Cooplink phone verification bot.

Flow:
1. /start <linking_token> — validate token, link chat_id to user, ask for phone
2. Contact message — verify contact.user_id == sender, generate code, dispatch Celery task
3. Fallback — any other message gets a helpful prompt
"""

import logging
import secrets
import uuid

from aiogram import Dispatcher, F
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from asgiref.sync import sync_to_async

from .keyboards import phone_request_keyboard, remove_keyboard

logger = logging.getLogger(__name__)

# Rate limits (per user, tracked via Django cache)
CODE_REQUEST_RATE_LIMIT = 3  # max codes per window
CODE_REQUEST_RATE_WINDOW = 15 * 60  # 15 minutes in seconds


@sync_to_async
def _get_user_from_linking_token(token_str: str):
    """
    Look up a valid, unexpired TelegramLinkingToken and return the associated user.
    Returns (token, user) or (None, None) if invalid/expired.
    """
    from notifications.models import TelegramLinkingToken

    try:
        token_uuid = uuid.UUID(token_str)
    except (ValueError, AttributeError):
        return None, None

    try:
        linking_token = TelegramLinkingToken.objects.select_related("user").get(token=token_uuid)
    except TelegramLinkingToken.DoesNotExist:
        return None, None

    if not linking_token.is_valid:
        return None, None

    return linking_token, linking_token.user


@sync_to_async
def _consume_linking_token(linking_token, chat_id: str):
    """Mark the linking token as consumed and update the user's telegram_chat_id."""
    from django.utils import timezone

    linking_token.telegram_chat_id = chat_id
    linking_token.consumed = True
    linking_token.consumed_at = timezone.now()
    linking_token.save(update_fields=["telegram_chat_id", "consumed", "consumed_at"])

    user = linking_token.user
    user.telegram_chat_id = chat_id
    user.save(update_fields=["telegram_chat_id"])
    return user


@sync_to_async
def _get_user_by_chat_id(chat_id: str):
    """Look up a user by their telegram_chat_id."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    try:
        return user_model.objects.get(telegram_chat_id=chat_id)
    except user_model.DoesNotExist:
        return None


@sync_to_async
def _check_code_rate_limit(user_id: int) -> bool:
    """
    Check if the user has exceeded the rate limit for code requests.
    Returns True if rate-limited (should NOT generate a new code).
    If Redis is unavailable, degrades gracefully by allowing the request.
    """
    from django.core.cache import cache

    cache_key = f"phone_code_requests:{user_id}"
    try:
        current = cache.get(cache_key, 0)
        if current >= CODE_REQUEST_RATE_LIMIT:
            return True
        cache.set(cache_key, current + 1, CODE_REQUEST_RATE_WINDOW)
    except Exception:
        # Redis unavailable — skip rate limiting rather than crashing.
        pass
    return False


@sync_to_async
def _check_phone_already_verified(phone_number: str, user_pk: int) -> bool:
    """Check if the phone number is already verified on another account."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return (
        user_model.objects.filter(phone_number=phone_number, phone_verified=True)
        .exclude(pk=user_pk)
        .exists()
    )


@sync_to_async
def _create_verification_code(user, code: str, phone_number: str, chat_id: str):
    """Create a verification code record."""
    from django.utils import timezone

    from notifications.models import PhoneVerificationCode

    return PhoneVerificationCode.objects.create(
        user=user,
        code=code,
        phone_number=phone_number,
        telegram_chat_id=chat_id,
        expires_at=timezone.now() + timezone.timedelta(minutes=5),
    )


def _generate_code() -> str:
    """Generate a cryptographically random 6-digit numeric code."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def handle_start(message: Message, command: CommandObject):
    """
    Handle /start <linking_token>.

    Validates the linking token, stores the Telegram chat_id mapping,
    and prompts the user to share their phone number via reply keyboard.
    """
    payload = command.args

    if not payload:
        await message.answer(
            "\u26a0\ufe0f This bot is used for phone verification on Cooplink.\n\n"
            "To get started, go to your Cooplink settings and click "
            '"<b>Verify phone via Telegram</b>" — it will open this bot '
            "with a special link."
        )
        return

    linking_token, user = await _get_user_from_linking_token(payload)

    if linking_token is None:
        await message.answer(
            "\u274c This verification link is <b>invalid or expired</b>.\n\n"
            "Go back to your Cooplink settings and request a new link."
        )
        return

    chat_id = str(message.chat.id)
    user = await _consume_linking_token(linking_token, chat_id)

    await message.answer(
        f"\U0001f44b Hi <b>{user.username or user.full_legal_name or 'there'}</b>!\n\n"
        "Your Cooplink account has been linked.\n\n"
        "To verify your phone number, tap the button below to share it with Telegram.\n"
        "Your phone number will be used <b>only</b> for verification purposes.",
        reply_markup=phone_request_keyboard(),
    )


async def handle_contact(message: Message):
    """
    Handle incoming contact (phone number sharing).

    Security: Verify that contact.user_id matches message.from_user.id
    to prevent users from forwarding someone else's contact card.
    """
    contact = message.contact

    # SECURITY: Verify the contact belongs to the sender
    if contact.user_id != message.from_user.id:
        await message.answer(
            "\u26a0\ufe0f The phone number you shared doesn't belong to your Telegram account.\n\n"
            "For security, you can only verify <b>your own</b> phone number.\n"
            "Please tap the button below again to share your actual phone number.",
            reply_markup=phone_request_keyboard(),
        )
        return

    chat_id = str(message.chat.id)
    user = await _get_user_by_chat_id(chat_id)

    if user is None:
        await message.answer(
            "\u274c Your Telegram account is not linked to any Cooplink account.\n\n"
            'Go to your Cooplink settings and click "<b>Verify phone via Telegram</b>" first.'
        )
        return

    # Check rate limit
    if await _check_code_rate_limit(user.id):
        await message.answer(
            "\u23f3 You've requested too many codes recently.\n\n"
            "Please wait a few minutes before requesting a new one."
        )
        return

    # Normalize phone number
    phone_number = contact.phone_number
    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    # Check if already verified on another account
    if await _check_phone_already_verified(phone_number, user.pk):
        await message.answer(
            "\u274c This phone number is already verified on another Cooplink account.\n\n"
            "Each phone number can only be linked to one account."
        )
        return

    # Generate verification code
    code = _generate_code()
    await _create_verification_code(user, code, phone_number, chat_id)

    # Dispatch Celery task to send the code
    from notifications.tasks import send_verification_code_task

    send_verification_code_task.delay(chat_id, code)

    await message.answer(
        "\u2705 Phone number received!\n\n"
        "Your verification code has been generated and will be sent to you shortly.\n"
        "The code expires in <b>5 minutes</b>.\n\n"
        "Enter the code on the Cooplink page to complete verification.",
        reply_markup=remove_keyboard(),
    )


async def handle_fallback(message: Message):
    """Handle any message that doesn't match other handlers."""
    await message.answer(
        "\U0001f916 This bot is used for phone verification on Cooplink.\n\n"
        "To verify your phone number, go to your Cooplink settings and click "
        '"<b>Verify phone via Telegram</b>".'
    )


def register_handlers(dp: Dispatcher):
    """Register all message handlers on the Dispatcher."""
    dp.message.register(handle_start, CommandStart())
    dp.message.register(handle_contact, F.contact)
    dp.message.register(handle_fallback)
