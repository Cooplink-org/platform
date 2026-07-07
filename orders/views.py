import logging
from decimal import Decimal

from django.db.models import F
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from listings.models import Project, ProjectSnapshot
from .models import Order
from payments.mirpay import MirPayClient

log = logging.getLogger(__name__)
mirpay = MirPayClient()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_create(request):
    """
    POST /api/orders/
    Create a new order for a published project and initiate payment with MirPay.
    Returns the order details and the payment redirect URL.
    """
    project_id = request.data.get("project_id")
    if not project_id:
        return Response({"detail": "project_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    project = get_object_or_404(Project, pk=project_id, status=Project.Status.PUBLISHED)

    # 1. Calculate financial splits
    price = project.price
    # Default platform fee is 10%, but can be dynamic in future.
    # We snapshot it on the Order so history doesn't change later.
    fee_percent = Decimal("10.00")
    fee_amount = (price * fee_percent / Decimal("100.00")).quantize(Decimal("1.00"))
    seller_earning = price - fee_amount

    # 2. Create the Order in pending_payment status
    order = Order.objects.create(
        buyer=request.user,
        project=project,
        seller=project.seller,
        price_at_purchase=price,
        platform_fee_percent=fee_percent,
        platform_fee_amount=fee_amount,
        seller_earning_amount=seller_earning,
        status=Order.Status.PENDING_PAYMENT,
    )

    # 3. Initiate MirPay payment
    try:
        payid, redirect_url, raw_response = mirpay.create_payment(order)
        # The mirpay client already handled the field mapping for payid and redirect_url.

        if not payid or not redirect_url:
            log.error("MirPay create_payment returned malformed data: %s", raw_response)
            return Response(
                {"detail": "Payment gateway returned malformed response. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY
            )

        order.payment_ref = payid
        order.save(update_fields=["payment_ref"])

        return Response({
            "id": order.id,
            "status": order.status,
            "price": str(order.price_at_purchase),
            "redirect_url": redirect_url,
        }, status=status.HTTP_201_CREATED)

    except Exception as exc:
        log.error("Failed to create MirPay payment for order %s: %s", order.id, exc)
        return Response(
            {"detail": "Could not initiate payment with MirPay. Please try again later."},
            status=status.HTTP_502_BAD_GATEWAY
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_download(request, pk):
    """
    GET /api/orders/{id}/download/
    Authenticated — only the buyer of a paid Order may download.
    Streams the latest ProjectSnapshot archive for the ordered project.
    """
    order = get_object_or_404(Order, pk=pk)

    if order.buyer != request.user:
        return Response({"detail": "You are not the buyer of this order."}, status=403)

    if order.status != Order.Status.PAID:
        return Response({"detail": f"Order is {order.get_status_display()}. Only paid orders can be downloaded."}, status=400)

    snapshot = (
        ProjectSnapshot.objects.filter(project=order.project)
        .order_by("-version")
        .first()
    )

    if not snapshot or not snapshot.archive:
        raise Http404("No snapshot archive available for this project.")

    Project.objects.filter(pk=order.project_id).update(download_count=F("download_count") + 1)

    try:
        response = FileResponse(
            snapshot.archive.open("rb"),
            as_attachment=True,
            filename=f"{order.project.slug}-v{snapshot.version}.zip",
        )
        return response
    except Exception as exc:
        log.error("Failed to stream snapshot %s: %s", snapshot.pk, exc)
        return Response({"detail": "Failed to read snapshot archive."}, status=500)
