from datetime import timedelta

from django.contrib import admin, messages
from django.db import transaction as db_transaction
from django.utils import timezone
from unfold.admin import ModelAdmin

from .models import Order, Transaction

# ═══════════════════════════════════════════════════════════════════════════════
#  Transaction — read-only ledger.  Nothing here should be editable by hand.
# ═══════════════════════════════════════════════════════════════════════════════


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = ("user", "type", "amount", "order", "created_at")
    list_filter = ("type", "created_at")
    search_fields = ("user__username",)
    readonly_fields = (
        "user",
        "type",
        "amount",
        "order",
        "created_at",
    )

    def has_add_permission(self, _request):
        """Ledger entries are created programmatically — never by hand."""
        return False

    def has_change_permission(self, _request, _obj=None):
        """Ledger is immutable."""
        return False

    def has_delete_permission(self, _request, _obj=None):
        """Ledger entries must never be removed."""
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Order — with a "Refund selected" admin action
# ═══════════════════════════════════════════════════════════════════════════════


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = (
        "buyer",
        "project",
        "seller",
        "status",
        "price_at_purchase",
        "downloaded_at",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "buyer__username",
        "seller__username",
        "project__title",
        "payment_ref",
    )
    readonly_fields = ("created_at", "paid_at", "downloaded_at")
    autocomplete_fields = ["buyer", "seller", "project"]
    date_hierarchy = "created_at"

    # ── admin action: Refund selected orders ─────────────────────────────

    @admin.action(description="Refund selected orders")
    def refund_selected(self, request, queryset):
        """
        Reverse the sale_earning and platform_fee transactions for each
        selected order that is still within the 7-day frozen window.

        Orders whose earning has already left the freeze window are skipped
        with a per-row warning, because the funds may have been paid out
        and the refund requires a manual conversation with the seller.
        """
        freeze = timedelta(days=7)
        now = timezone.now()
        refunded = 0
        skipped_not_paid = []
        skipped_past_window = []

        for order in queryset.select_related("buyer", "seller", "project"):
            # Only paid orders can be refunded
            if order.status != Order.Status.PAID:
                skipped_not_paid.append(order)
                continue

            # Find the original sale_earning transaction for this order
            earning_tx = (
                Transaction.objects.filter(
                    order=order,
                    type=Transaction.Type.SALE_EARNING,
                )
                .order_by("created_at")
                .first()
            )

            if not earning_tx:
                skipped_not_paid.append(order)
                continue

            # Check whether the earning is still within the 7-day freeze
            if earning_tx.created_at + freeze < now:
                skipped_past_window.append(order)
                continue

            # Inside freeze window → safe to refund automatically
            self._execute_refund(order, earning_tx)
            refunded += 1

        # ── report results ───────────────────────────────────────────────
        if refunded:
            self.message_user(
                request,
                f"{refunded} order(s) refunded successfully.",
                level=messages.SUCCESS,
            )

        if skipped_not_paid:
            ids = ", ".join(f"#{o.id}" for o in skipped_not_paid)
            self.message_user(
                request,
                f"Skipped (not in 'paid' status or missing earning): {ids}.",
                level=messages.WARNING,
            )

        if skipped_past_window:
            ids = ", ".join(f"#{o.id}" for o in skipped_past_window)
            self.message_user(
                request,
                f"Skipped (outside 7-day freeze window — the seller's payout "
                f"may already have gone out; this needs a manual conversation "
                f"with the seller instead): {ids}.",
                level=messages.ERROR,
            )

    @db_transaction.atomic
    def _execute_refund(self, order, earning_tx):
        """
        Create reversing REFUND entries for both the sale_earning and
        platform_fee, then mark the order as refunded.
        """
        # Reverse the sale_earning
        Transaction.objects.create(
            user=order.seller,
            order=order,
            type=Transaction.Type.REFUND,
            amount=earning_tx.amount,
        )

        # Reverse the platform_fee
        fee_tx = Transaction.objects.filter(
            order=order,
            type=Transaction.Type.PLATFORM_FEE,
        ).first()
        if fee_tx:
            Transaction.objects.create(
                user=order.seller,
                order=order,
                type=Transaction.Type.REFUND,
                amount=fee_tx.amount,
            )

        order.status = Order.Status.REFUNDED
        order.save(update_fields=["status"])

    actions = ["refund_selected"]
