import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from payments.inpay import InPayClient
from payments.models import PaymentProviderConfig

from .models import LeaderboardEntry, LeaderboardSettings

log = logging.getLogger(__name__)

MAX_ENTRIES_SHOWN = 50


def _serialize_settings():
    cfg = LeaderboardSettings.load()
    return {
        "enabled": cfg.enabled,
        "min_amount_uzs": str(cfg.min_amount_uzs),
    }


def _entry_payload(entry, position=None):
    data = {
        "id": entry.id,
        "domain": entry.domain,
        "brand_name": entry.brand_name,
        "description": entry.description,
        "logo_url": entry.logo_url,
        "amount_uzs": str(entry.amount_uzs),
        "status": entry.status,
        "category": entry.category,
        "likes": entry.likes,
        "clicks": entry.clicks,
        "created_at": entry.created_at,
    }
    if position is not None:
        data["position"] = position
    return data


@api_view(["GET"])
@permission_classes([AllowAny])
def leaderboard(_request):
    """GET /api/leaderboard/

    Public: the paid entries in rank order plus totals and settings.
    """
    entries = LeaderboardEntry.ranked()[:MAX_ENTRIES_SHOWN]
    started_at = LeaderboardEntry.started_at()
    return Response(
        {
            "settings": _serialize_settings(),
            "entries": [_entry_payload(e, position=i + 1) for i, e in enumerate(entries)],
            "total_earned_uzs": str(LeaderboardEntry.total_earned()),
            "started_at": started_at,
            "count": LeaderboardEntry.objects.filter(status=LeaderboardEntry.Status.PAID).count(),
        }
    )


def _clean_amount(raw):
    try:
        amount = Decimal(str(raw).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount <= 0:
        return None
    return amount.quantize(Decimal("1.00"))


@api_view(["POST"])
@permission_classes([AllowAny])
def entry_create(request):
    """POST /api/leaderboard/entries/

    Step 1+2 of the flow: domain + brand details + bid amount.
    Creates a pending entry and returns the position it will hold once paid.
    """
    cfg = LeaderboardSettings.load()
    if not cfg.enabled:
        return Response({"detail": "Leaderboard is disabled."}, status=status.HTTP_403_FORBIDDEN)

    domain = str(request.data.get("domain", "")).strip().lower()
    brand_name = str(request.data.get("brand_name", "")).strip()
    description = str(request.data.get("description", "")).strip()
    logo_url = str(request.data.get("logo_url", "")).strip()
    amount = _clean_amount(request.data.get("amount_uzs"))
    category = (
        str(request.data.get("category", "")).strip().lower() or LeaderboardEntry.Category.TECH
    )

    errors = {}
    if not domain or "." not in domain:
        errors["domain"] = "Valid domain is required (e.g. acme.uz)."
    if not brand_name:
        errors["brand_name"] = "Brand name is required."
    if len(description) > 280:
        errors["description"] = "Description must be at most 280 characters."
    if amount is None:
        errors["amount_uzs"] = "Enter a valid amount in UZS."
    elif amount < cfg.min_amount_uzs:
        errors["amount_uzs"] = f"Minimum bid is {cfg.min_amount_uzs} UZS."
    if category not in LeaderboardEntry.Category.values:
        errors["category"] = "Unknown category."
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    entry = LeaderboardEntry.objects.create(
        domain=domain,
        brand_name=brand_name,
        description=description,
        logo_url=logo_url,
        amount_uzs=amount,
        category=category,
        user=request.user if request.user.is_authenticated else None,
    )

    return Response(
        {
            "entry": _entry_payload(entry, position=LeaderboardEntry.prospective_position(amount)),
            "position": LeaderboardEntry.prospective_position(amount),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def entry_pay(request, pk):
    """POST /api/leaderboard/entries/<id>/pay/

    Creates an inPAY payment for the entry and returns the checkout URL.
    The buyer lands back on the /crack-it page, which verifies via
    POST /api/leaderboard/verify/.
    """
    cfg = LeaderboardSettings.load()
    if not cfg.enabled:
        return Response({"detail": "Leaderboard is disabled."}, status=status.HTTP_403_FORBIDDEN)

    entry = LeaderboardEntry.objects.filter(
        pk=pk, status=LeaderboardEntry.Status.PENDING_PAYMENT
    ).first()
    if not entry:
        return Response(
            {"detail": "Entry not found or already paid."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not PaymentProviderConfig.objects.filter(
        provider=PaymentProviderConfig.Provider.INPAY, enabled=True
    ).exists():
        return Response(
            {"detail": "inPAY is not available right now. Please try again later."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    frontend = settings.FRONTEND_URL.rstrip("/")
    return_url = f"{frontend}/crack-it?entry={entry.id}&payment=return"

    try:
        client = InPayClient()
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        client_ip = (
            forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
        )
        payid, redirect_url, _raw = client.create_payment(
            entry,
            client_ip=client_ip,
            amount=entry.amount_uzs,
            description=f"Crack It #{entry.id}",
            return_url=return_url,
        )
    except Exception as exc:
        log.error("inPAY payment creation failed for leaderboard entry %s: %s", entry.id, exc)
        return Response(
            {"detail": f"Could not start payment: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not payid or not redirect_url:
        return Response(
            {"detail": "Payment gateway returned malformed response. Please try again later."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    entry.payment_ref = payid
    entry.save(update_fields=["payment_ref"])

    return Response(
        {
            "entry_id": entry.id,
            "payid": payid,
            "redirect_url": redirect_url,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def entry_click(_request, pk):
    """POST /api/leaderboard/entries/<id>/click/

    Public click counter — the frontend fires this when a visitor opens an
    entry's outbound domain link. Fire-and-forget friendly: returns the new
    click total.
    """
    updated = LeaderboardEntry.objects.filter(pk=pk).update(clicks=F("clicks") + 1)
    if not updated:
        return Response({"detail": "Entry not found."}, status=status.HTTP_404_NOT_FOUND)
    clicks = LeaderboardEntry.objects.filter(pk=pk).values_list("clicks", flat=True).first()
    return Response({"clicks": clicks})


@db_transaction.atomic
def confirm_entry(entry):
    """Mark an entry paid. Called from the inPAY webhook and the verify endpoint."""
    if entry.status == LeaderboardEntry.Status.PAID:
        return entry
    entry.status = LeaderboardEntry.Status.PAID
    entry.paid_at = timezone.now()
    entry.save(update_fields=["status", "paid_at"])
    return entry


@api_view(["POST"])
@permission_classes([AllowAny])
def verify(request):
    """POST /api/leaderboard/verify/

    Public verification after the buyer returns from inPAY checkout.
    Accepts the inPAY order_id (payment_ref) or the leaderboard entry id
    (the /crack-it return URL carries the entry id).
    Independently checks the payment status with inPAY before confirming.
    """
    ref = request.data.get("order_id") or request.data.get("payid")
    entry_id = request.data.get("entry_id")

    if ref:
        entry = LeaderboardEntry.objects.filter(payment_ref=str(ref)).first()
    elif entry_id:
        try:
            entry = LeaderboardEntry.objects.filter(pk=int(entry_id)).first()
        except (TypeError, ValueError):
            entry = None
        if entry:
            ref = entry.payment_ref
    else:
        return Response({"error": "order_id or entry_id is required"}, status=400)

    if not entry or not ref:
        return Response({"status": "unknown", "detail": "Entry not found for this payment."})

    if entry.status == LeaderboardEntry.Status.PAID:
        return Response(
            {"status": "paid", "entry": _entry_payload(entry, position=_position_of(entry))}
        )

    try:
        client = InPayClient()
        verification = client.check_status(ref)
    except Exception as exc:
        log.error("inPAY check_status failed for leaderboard ref=%s: %s", ref, exc)
        return Response({"error": str(exc)}, status=502)

    verified_status = str(verification.get("status", "")).strip().lower()
    if verified_status == "success":
        confirm_entry(entry)
        return Response(
            {"status": "paid", "entry": _entry_payload(entry, position=_position_of(entry))}
        )
    if verified_status in ("failed", "cancelled"):
        entry.status = LeaderboardEntry.Status.FAILED
        entry.save(update_fields=["status"])
        return Response({"status": "failed", "entry": _entry_payload(entry)})

    return Response({"status": entry.status, "entry": _entry_payload(entry)})


def _position_of(entry):
    paid = LeaderboardEntry.ranked()
    for i, e in enumerate(paid):
        if e.id == entry.id:
            return i + 1
    return None
