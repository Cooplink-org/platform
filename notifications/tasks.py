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
    message_type: listing_approved, listing_rejected, sale_made, funds_unlocked, payout_processed, payout_rejected
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        log.error("Cannot notify: User %s does not exist", user_id)
        return

    chat_id = user.telegram_chat_id
    
    # Templates
    templates = {
        "listing_approved": "✅ Your listing '{title}' has been approved and is now live!",
        "listing_rejected": "❌ Your listing '{title}' was rejected. Reason: {reason}",
        "sale_made": "💰 Great news! You just made a sale! '{title}' was purchased for {amount} UZS.",
        "funds_unlocked": "🔓 {amount} UZS from your sale of '{title}' is now available for payout!",
        "payout_completed": "💸 Your payout of {amount} UZS has been completed successfully.",
        "payout_rejected": "⚠️ Your payout request of {amount} UZS was rejected. Note: {reason}",
    }

    template = templates.get(message_type, "New notification: {message}")
    text = template.format(**context)

    if chat_id:
        send_telegram_message(chat_id, text)
    else:
        log.info("User %s (%s) has no telegram_chat_id linked. Notification logged: %s", 
                 user.username, user_id, text)

@shared_task
def daily_check_unlocked_earnings():
    """
    Daily job to find SALE_EARNING transactions that just turned 7 days old
    and notify their owners.
    """
    from datetime import timedelta
    from django.utils import timezone
    from orders.models import Transaction

    # We look for transactions created exactly between 7 and 8 days ago
    # to avoid double-notifying or missing people if the job runs once a day.
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
            {"amount": f"{tx.amount:,.2f}", "title": project_title}
        )
