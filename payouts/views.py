from decimal import ROUND_DOWN, Decimal

from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.utils import encrypt_token
from orders.balance import available_balance, pending_balance

from .models import PayoutFeeConfig, PayoutRequest


class PayoutRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutRequest
        fields = (
            "id",
            "amount",
            "payout_fee_percent",
            "payout_fee_amount",
            "net_amount",
            "destination_card_last4",
            "status",
            "admin_note",
            "requested_at",
            "processed_at",
        )
        read_only_fields = fields


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payout_request_create(request):
    """
    POST /api/payouts/request/
    Seller submits a payout request. Validates amount <= available_balance.
    Encrypts the card number before storing — only last4 is ever returned.
    A configurable withdrawal fee (admin-settable %) is deducted from the amount.
    """
    user = request.user

    try:
        amount = Decimal(str(request.data.get("amount", 0)))
    except Exception:
        return Response({"detail": "Invalid amount."}, status=400)

    if amount <= 0:
        return Response({"detail": "Amount must be greater than zero."}, status=400)

    avail = available_balance(user)
    if amount > avail:
        return Response(
            {"detail": f"Insufficient available balance. Available: {avail}, requested: {amount}."},
            status=400,
        )

    # Apply the admin-configurable withdrawal fee (snapshotted onto the request)
    fee_percent = PayoutFeeConfig.get_fee_percent()
    fee_amount = (amount * fee_percent / Decimal("100.00")).quantize(
        Decimal("1.00"), rounding=ROUND_DOWN
    )
    net_amount = amount - fee_amount

    card_number = str(request.data.get("card_number", ""))
    if len(card_number) < 10:
        return Response({"detail": "Invalid card number."}, status=400)

    last4 = card_number[-4:]

    payout = PayoutRequest.objects.create(
        seller=user,
        amount=amount,
        payout_fee_percent=fee_percent,
        payout_fee_amount=fee_amount,
        net_amount=net_amount,
        destination_card_encrypted=encrypt_token(card_number),
        destination_card_last4=last4,
    )

    return Response(PayoutRequestSerializer(payout).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payout_list_mine(request):
    """
    GET /api/payouts/mine/
    Returns the authenticated seller's payout history and balance summary.
    """
    user = request.user
    qs = PayoutRequest.objects.filter(seller=user).order_by("-requested_at")
    return Response(
        {
            "available_balance": available_balance(user),
            "pending_balance": [
                {"amount": p["amount"], "unlocks_at": p["unlocks_at"]}
                for p in pending_balance(user)
            ],
            "withdrawal_fee_percent": PayoutFeeConfig.get_fee_percent(),
            "payouts": PayoutRequestSerializer(qs, many=True).data,
        }
    )
