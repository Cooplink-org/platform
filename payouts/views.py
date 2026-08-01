from decimal import Decimal

from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.utils import encrypt_token
from orders.balance import available_balance, pending_balance

from .models import PayoutRequest


class PayoutRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutRequest
        fields = (
            "id",
            "amount",
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

    card_number = str(request.data.get("card_number", ""))
    if len(card_number) < 10:
        return Response({"detail": "Invalid card number."}, status=400)

    last4 = card_number[-4:]

    payout = PayoutRequest.objects.create(
        seller=user,
        amount=amount,
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
            "payouts": PayoutRequestSerializer(qs, many=True).data,
        }
    )
