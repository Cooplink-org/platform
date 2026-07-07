from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import Transaction


def _refund_sum(user):
    result = Transaction.objects.filter(
        user=user, type=Transaction.Type.REFUND
    ).aggregate(total=Sum("amount"))
    return result["total"] or 0


def _payout_sum(user):
    result = Transaction.objects.filter(
        user=user, type=Transaction.Type.PAYOUT
    ).aggregate(total=Sum("amount"))
    return result["total"] or 0


def available_balance(user):
    cutoff = timezone.now() - timedelta(days=7)
    earned = (
        Transaction.objects.filter(
            user=user,
            type=Transaction.Type.SALE_EARNING,
            created_at__lte=cutoff,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    return earned - _refund_sum(user) - _payout_sum(user)


def pending_balance(user):
    cutoff = timezone.now() - timedelta(days=7)
    qs = Transaction.objects.filter(
        user=user,
        type=Transaction.Type.SALE_EARNING,
        created_at__gt=cutoff,
    ).order_by("created_at")

    items = []
    for tx in qs:
        items.append({
            "amount": tx.amount,
            "created_at": tx.created_at,
            "unlocks_at": tx.created_at + timedelta(days=7),
        })
    return items
