import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from .telegram import send_telegram_message

log = logging.getLogger(__name__)
User = get_user_model()


@shared_task
def notify_user_task(user_id, message_type, context):
    """
    Celery task to send personalized notifications to users.
    message_type: listing_approved, listing_rejected, sale_made, funds_unlocked,
    payout_processed, payout_rejected
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        log.error("Cannot notify: User %s does not exist", user_id)
        return

    chat_id = user.telegram_chat_id

    # Templates
    templates = {
        "listing_approved": "\u2705 Your listing '{title}' has been approved and is now live!",
        "listing_rejected": "\u274c Your listing '{title}' was rejected. Reason: {reason}",
        "sale_made": (
            "\U0001f4b0 Great news! You just made a sale! '{title}' was purchased for {amount} UZS."
        ),
        "funds_unlocked": (
            "\U0001f513 {amount} UZS from your sale of '{title}' is now available for payout!"
        ),
        "payout_completed": (
            "\U0001f4b8 Your payout of {amount} UZS has been completed successfully."
        ),
        "payout_rejected": (
            "\u26a0\ufe0f Your payout request of {amount} UZS was rejected. Note: {reason}"
        ),
    }

    template = templates.get(message_type, "New notification: {message}")
    text = template.format(**context)

    if chat_id:
        send_telegram_message(chat_id, text)
    else:
        log.info(
            "User %s (%s) has no telegram_chat_id linked. Notification logged: %s",
            user.username,
            user_id,
            text,
        )


@shared_task
def daily_check_unlocked_earnings():
    """
    Daily job to find SALE_EARNING transactions that just turned 7 days old
    and notify their owners.
    """
    from datetime import timedelta

    from django.utils import timezone

    from orders.models import Transaction

    now = timezone.now()
    start = now - timedelta(days=8)
    end = now - timedelta(days=7)

    txs = Transaction.objects.filter(
        type=Transaction.Type.SALE_EARNING,
        created_at__gte=start,
        created_at__lt=end,
    ).select_related("user", "order__project")

    for tx in txs:
        project_title = tx.order.project.title if tx.order else "a project"
        notify_user_task.delay(
            tx.user_id,
            "funds_unlocked",
            {"amount": f"{tx.amount:,.2f}", "title": project_title},
        )


@shared_task
def send_verification_code_task(chat_id: str, code: str):
    """
    Send a phone verification code to a user via Telegram.
    Creates a fresh Bot instance per call to avoid event loop issues.
    """
    import asyncio
    import os

    async def _send():
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            log.error("TELEGRAM_BOT_TOKEN is not set")
            return

        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            await bot.send_message(
                chat_id,
                f"\U0001f510 Your Cooplink phone verification code:\n\n"
                f"<code>{code}</code>\n\n"
                f"This code expires in <b>5 minutes</b>.\n"
                f"Enter it on the Cooplink page to complete verification.\n\n"
                f"<i>Do not share this code with anyone.</i>",
            )
            log.info("Verification code sent to chat %s", chat_id)
        except Exception as exc:
            log.error("Failed to send verification code to chat %s: %s", chat_id, exc)
        finally:
            await bot.session.close()

    try:
        asyncio.run(_send())
    except Exception as exc:
        log.error("Error in send_verification_code_task: %s", exc)


@shared_task
def cleanup_expired_telegram_tokens():
    """
    Periodic cleanup task: delete expired/used linking tokens and
    expired/used verification codes older than 24 hours.
    Run hourly via Celery Beat.
    """
    from datetime import timedelta

    from django.utils import timezone

    from notifications.models import PhoneVerificationCode, TelegramLinkingToken

    now = timezone.now()

    # Delete linking tokens that are either consumed or expired (older than 24h)
    old_threshold = now - timedelta(hours=24)
    deleted_tokens = TelegramLinkingToken.objects.filter(
        q_consumed_or_expired(old_threshold)
    ).delete()
    log.info(
        "Cleaned up %d expired/used linking tokens", deleted_tokens[0] if deleted_tokens else 0
    )

    # Delete verification codes that are used or expired (older than 24h)
    deleted_codes = PhoneVerificationCode.objects.filter(q_used_or_expired(old_threshold)).delete()
    log.info(
        "Cleaned up %d expired/used verification codes", deleted_codes[0] if deleted_codes else 0
    )


def q_consumed_or_expired(threshold):
    """Build Q filter for consumed or expired tokens older than threshold."""
    from django.db.models import Q

    return Q(consumed=True, consumed_at__lt=threshold) | Q(expires_at__lt=threshold)


def q_used_or_expired(threshold):
    """Build Q filter for used or expired codes older than threshold."""
    from django.db.models import Q

    return Q(used=True, used_at__lt=threshold) | Q(expires_at__lt=threshold)
