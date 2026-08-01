"""
Signal handlers that create in-platform Notification records whenever
a relevant event occurs on the platform.

Connected in NotificationsConfig.ready() so they register on app startup.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


def _create(recipient, ntype, title, body="", link=""):
    """Safe wrapper — never let notification creation crash the main request."""
    try:
        from .models import Notification

        with transaction.atomic():
            Notification.objects.create(
                recipient=recipient,
                type=ntype,
                title=title,
                body=body,
                link=link,
            )
    except Exception as exc:
        log.exception(
            "Failed to create notification (%s) for user %s: %s",
            ntype,
            getattr(recipient, "pk", "?"),
            exc,
        )


# ── Q&A ───────────────────────────────────────────────────────────────────────


@receiver(post_save, sender="listings.ProjectQA")
def on_qa_saved(sender, instance, created, **_kwargs):  # noqa: ARG001
    from .models import Notification

    if created:
        # New question — notify the seller
        seller = instance.project.seller
        if seller != instance.user:
            _create(
                recipient=seller,
                ntype=Notification.Type.QA_ASKED,
                title=f'New question on "{instance.project.title}"',
                body=instance.question[:200],
                link=f"/projects/{instance.project.slug}",
            )
    else:
        # Existing QA updated — check if an answer was just added
        # notify the asker (not the seller answering their own question)
        if instance.answer and instance.answered_at and instance.user != instance.project.seller:
            _create(
                recipient=instance.user,
                ntype=Notification.Type.QA_ANSWERED,
                title=f'Your question on "{instance.project.title}" was answered',
                body=instance.answer[:200],
                link=f"/projects/{instance.project.slug}",
            )


# ── Reviews ───────────────────────────────────────────────────────────────────


@receiver(post_save, sender="listings.Rating")
def on_rating_saved(sender, instance, created, **_kwargs):  # noqa: ARG001
    from .models import Notification

    if created:
        seller = instance.project.seller
        if seller != instance.user:
            stars = "★" * instance.score + "☆" * (5 - instance.score)
            _create(
                recipient=seller,
                ntype=Notification.Type.REVIEW_RECEIVED,
                title=f'New {stars} review on "{instance.project.title}"',
                body=(instance.review_text or "")[:200],
                link=f"/projects/{instance.project.slug}",
            )


# ── Listing status changes ────────────────────────────────────────────────────


@receiver(post_save, sender="listings.Project")
def on_project_saved(sender, instance, created, **kwargs):  # noqa: ARG001
    from .models import Notification

    if created:
        return  # don't notify on initial draft creation

    update_fields = kwargs.get("update_fields")
    # Only fire when status was explicitly changed
    if update_fields and "status" not in update_fields:
        return

    status = instance.status
    if status == "published":
        _create(
            recipient=instance.seller,
            ntype=Notification.Type.LISTING_APPROVED,
            title=f'Your listing "{instance.title}" was approved!',
            body="It's now live on the marketplace.",
            link=f"/projects/{instance.slug}",
        )
    elif status == "rejected":
        _create(
            recipient=instance.seller,
            ntype=Notification.Type.LISTING_REJECTED,
            title=f'Your listing "{instance.title}" was rejected',
            body="Please check your dashboard for details and resubmit after making changes.",
            link="/dashboard/listings",
        )


# ── Orders / sales ────────────────────────────────────────────────────────────


@receiver(post_save, sender="orders.Order")
def on_order_saved(sender, instance, created, **kwargs):  # noqa: ARG001
    from .models import Notification

    update_fields = kwargs.get("update_fields")
    # Only fire when status field was explicitly changed (not on creation)
    if created:
        return
    if update_fields and "status" not in update_fields:
        return

    if instance.status == "paid":
        price = f"{instance.price_at_purchase:,.0f} UZS"
        # Notify seller of a sale
        _create(
            recipient=instance.seller,
            ntype=Notification.Type.SALE_MADE,
            title=f'You made a sale — "{instance.project.title}"',
            body=f"{instance.buyer.username} purchased your listing for {price}.",
            link="/dashboard",
        )
        # Notify buyer that order is confirmed
        _create(
            recipient=instance.buyer,
            ntype=Notification.Type.ORDER_PLACED,
            title=f'Order confirmed — "{instance.project.title}"',
            body=f"Payment of {price} received. Visit your library to access the files.",
            link="/library",
        )


# ── Moderation outcomes (report actioned / dismissed) ────────────────────────


@receiver(post_save, sender="moderation.ModerationLog")
def on_moderation_log_saved(sender, instance, created, **_kwargs):  # noqa: ARG001
    from moderation.models import ModerationLog

    from .models import Notification

    if not created:
        return

    report = instance.report
    if not report or not report.reporter_id:
        return

    if instance.action == ModerationLog.Action.ACTION_REPORT:
        target_label = (
            f'"{report.project.title}"'
            if report.project
            else f"user @{report.reported_user.username}"
            if report.reported_user
            else "the reported content"
        )
        _create(
            recipient=report.reporter,
            ntype=Notification.Type.REPORT_ACTIONED,
            title="Your report was actioned",
            body=f"Moderators have taken action on {target_label}.",
            link="/",
        )
    elif instance.action == ModerationLog.Action.DISMISS_REPORT:
        _create(
            recipient=report.reporter,
            ntype=Notification.Type.REPORT_DISMISSED,
            title="Your report was reviewed",
            body="After review, moderators determined no action was necessary.",
            link="/",
        )
