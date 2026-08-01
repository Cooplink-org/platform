from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from unfold.admin import ModelAdmin

from accounts.utils import decrypt_token
from orders.models import Transaction

from .models import PayoutRequest


@admin.register(PayoutRequest)
class PayoutRequestAdmin(ModelAdmin):
    list_display = ("seller", "amount", "destination_card_last4", "status", "requested_at")
    list_filter = ("status",)
    search_fields = ("seller__username", "destination_card_last4")
    readonly_fields = (
        "seller",
        "amount",
        "destination_card_last4",
        "requested_at",
        "processed_at",
        "processed_by",
    )
    autocomplete_fields = ["seller"]

    fieldsets = (
        (
            None,
            {
                "fields": ("seller", "amount", "status", "admin_note"),
            },
        ),
        (
            "Card information",
            {
                "fields": ("destination_card_last4", "_decrypted_card_display"),
                "description": (
                    "The full decrypted card number is displayed below. "
                    "This is the only place in the system where the full number is visible."
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("requested_at", "processed_at", "processed_by"),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        fs = super().get_fieldsets(request, obj)
        if obj is None:
            return [(n, f) for n, f in fs if f["fields"][0] != "_decrypted_card_display"]
        return fs

    def get_readonly_fields(self, _request, _obj=None):
        rof = list(self.readonly_fields)
        if _obj is None:
            rof.append("status")
        return rof

    @admin.display(description="Full card number")
    def _decrypted_card_display(self, obj):
        if obj and obj.destination_card_encrypted:
            try:
                return decrypt_token(obj.destination_card_encrypted)
            except Exception:
                return "*** decryption error ***"
        return ""

    # ── admin URLs for intermediate confirmation pages ────────────────────

    def get_urls(self):
        base = super().get_urls()
        custom = [
            path(
                "<path:object_ids>/complete-payout/",
                self.admin_site.admin_view(self.complete_payout_view),
                name="payouts_payoutrequest_complete_payout",
            ),
            path(
                "<path:object_ids>/reject-payout/",
                self.admin_site.admin_view(self.reject_payout_view),
                name="payouts_payoutrequest_reject_payout",
            ),
        ]
        return custom + base

    def complete_payout_view(self, request, object_ids):
        ids = [int(i) for i in object_ids.split(",") if i.isdigit()]
        qs = PayoutRequest.objects.filter(id__in=ids)

        if request.method == "POST":
            count = 0
            for payout in qs.filter(
                status__in=[PayoutRequest.Status.REQUESTED, PayoutRequest.Status.PROCESSING]
            ):
                payout.status = PayoutRequest.Status.COMPLETED
                payout.processed_at = timezone.now()
                payout.processed_by = request.user
                payout.save(update_fields=["status", "processed_at", "processed_by"])

                Transaction.objects.create(
                    user=payout.seller,
                    type=Transaction.Type.PAYOUT,
                    amount=payout.amount,
                )

                # Notify seller
                from notifications.tasks import notify_user_task

                notify_user_task.delay(
                    payout.seller_id, "payout_completed", {"amount": f"{payout.amount:,.2f}"}
                )
                count += 1

            self.message_user(
                request,
                f"{count} payout(s) completed. "
                "Ensure the real-world transfer was executed before marking.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:payouts_payoutrequest_changelist"))

        return render(
            request,
            "admin/payouts/confirm_action.html",
            {
                "title": "Complete payout",
                "message": (
                    "Mark the selected payouts as completed. This will create PAYOUT "
                    "Transaction records and update their status."
                ),
                "warning": (
                    "⚠ The actual money movement (bank transfer / card-to-card) must be "
                    "done manually by an admin outside this system. This action only "
                    "records that the transfer was made — it does NOT initiate any "
                    "real payment."
                ),
                "action_name": "complete_payout",
                "queryset": qs,
                "opts": self.model._meta,
            },
        )

    def reject_payout_view(self, request, object_ids):
        ids = [int(i) for i in object_ids.split(",") if i.isdigit()]
        qs = PayoutRequest.objects.filter(id__in=ids)

        if request.method == "POST":
            admin_note = request.POST.get("admin_note", "")
            count = 0
            for payout in qs.filter(status=PayoutRequest.Status.REQUESTED):
                payout.status = PayoutRequest.Status.REJECTED
                payout.admin_note = admin_note
                payout.processed_at = timezone.now()
                payout.processed_by = request.user
                payout.save(update_fields=["status", "admin_note", "processed_at", "processed_by"])

                # Notify seller
                from notifications.tasks import notify_user_task

                notify_user_task.delay(
                    payout.seller_id,
                    "payout_rejected",
                    {
                        "amount": f"{payout.amount:,.2f}",
                        "reason": admin_note or "No reason provided.",
                    },
                )
                count += 1
            self.message_user(request, f"{count} payout(s) rejected.")
            return HttpResponseRedirect(reverse("admin:payouts_payoutrequest_changelist"))

        return render(
            request,
            "admin/payouts/confirm_action.html",
            {
                "title": "Reject payout",
                "message": (
                    "Reject the selected payout requests. "
                    "Provide a reason visible to the seller."
                ),
                "action_name": "reject_payout",
                "queryset": qs,
                "opts": self.model._meta,
                "show_admin_note": True,
            },
        )

    # ── admin actions (simple ones process inline) ────────────────────────

    @admin.action(description="Mark selected as processing")
    def mark_processing(self, request, queryset):
        updated = queryset.filter(status=PayoutRequest.Status.REQUESTED).update(
            status=PayoutRequest.Status.PROCESSING,
        )
        self.message_user(request, f"{updated} payout(s) marked as processing.")

    @admin.action(description="Complete selected payouts")
    def complete_payout(self, _request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return HttpResponseRedirect(
            reverse("admin:payouts_payoutrequest_complete_payout", args=[ids])
        )

    @admin.action(description="Reject selected payouts")
    def reject_payout(self, _request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return HttpResponseRedirect(
            reverse("admin:payouts_payoutrequest_reject_payout", args=[ids])
        )

    actions = ["mark_processing", "complete_payout", "reject_payout"]
