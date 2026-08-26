import logging
from decimal import Decimal

from django.conf import settings
from django.db.models import F
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from listings.models import Project, ProjectSnapshot
from payments.inpay import InPayClient, InPayError
from payments.mirpay import MirPayClient, MirPayError
from payments.models import PaymentProviderConfig

from .models import Order

log = logging.getLogger(__name__)
mirpay = MirPayClient()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_create(request):
    """
    POST /api/orders/
    Create a new order for a published project and initiate payment.
    Accepts an optional `payment_provider` param ("inpay" or "mirpay").
    If not specified, uses the default enabled provider from admin config,
    falling back to MirPay for backward compatibility.
    Returns the order details and the payment redirect URL.
    """
    project_id = request.data.get("project_id")
    if not project_id:
        return Response({"detail": "project_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    project = get_object_or_404(Project, pk=project_id, status=Project.Status.PUBLISHED)

    # 1. Determine which payment provider to use
    provider_name = request.data.get("payment_provider")
    if not provider_name:
        # Use the default enabled provider from admin config
        default_config = PaymentProviderConfig.objects.filter(enabled=True, is_default=True).first()
        provider_name = default_config.provider if default_config else Order.Provider.MIRPAY

    # Validate provider
    valid_providers = {Order.Provider.MIRPAY, Order.Provider.INPAY}
    if provider_name not in valid_providers:
        return Response(
            {"detail": f"Unknown payment provider: {provider_name}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check the chosen provider is enabled in admin config or configured via settings
    has_db_config = PaymentProviderConfig.objects.filter(
        provider=provider_name, enabled=True
    ).exists()
    has_env_config = provider_name == Order.Provider.MIRPAY and bool(
        getattr(settings, "MIRPAY_KASSA_ID", "")
    )

    if not (has_db_config or has_env_config):
        return Response(
            {
                "detail": (
                    f"{'inPAY' if provider_name == Order.Provider.INPAY else 'MirPay'} "
                    "is not available. Please use a different payment method."
                )
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # 2. Calculate financial splits
    price = project.price
    # Default platform fee is 10%, but can be dynamic in future.
    # We snapshot it on the Order so history doesn't change later.
    fee_percent = Decimal("10.00")
    fee_amount = (price * fee_percent / Decimal("100.00")).quantize(Decimal("1.00"))
    seller_earning = price - fee_amount

    # 3. Create the Order in pending_payment status
    order = Order.objects.create(
        buyer=request.user,
        project=project,
        seller=project.seller,
        price_at_purchase=price,
        platform_fee_percent=fee_percent,
        platform_fee_amount=fee_amount,
        seller_earning_amount=seller_earning,
        status=Order.Status.PENDING_PAYMENT,
        provider=provider_name,
    )

    # 4. Initiate payment with the selected provider
    try:
        if provider_name == Order.Provider.INPAY:
            client_ip = _extract_client_ip(request)
            payid, redirect_url, raw_response = _create_inpay_payment(order, client_ip=client_ip)
        else:
            payid, redirect_url, raw_response = _create_mirpay_payment(order)

        if not payid:
            log.error(
                "Payment provider %s returned no payment id for order %s: %s",
                provider_name,
                order.id,
                raw_response,
            )
            return Response(
                {"detail": "Payment gateway returned malformed response. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order.payment_ref = payid
        order.save(update_fields=["payment_ref"])

        log.info(
            "Payment created for order %s via %s: ref=%s, redirect_url=%s",
            order.id,
            provider_name,
            payid,
            redirect_url,
        )

        return Response(
            {
                "id": order.id,
                "status": order.status,
                "price": str(order.price_at_purchase),
                "provider": provider_name,
                "redirect_url": redirect_url or "",
                "payid": payid,
            },
            status=status.HTTP_201_CREATED,
        )

    except InPayError as exc:
        log.error("inPAY unavailable for order %s: %s", order.id, exc)
        return Response(
            {
                "detail": (
                    "inPAY is not available. Please whitelist your server IP "
                    "in the inPAY dashboard (Strict mode) or choose MirPay. "
                    f"Error: {exc}"
                )
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except MirPayError as exc:
        log.error("MirPay error for order %s: %s", order.id, exc)
        return Response(
            {"detail": f"MirPay payment error: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:
        log.error("Failed to create payment for order %s via %s: %s", order.id, provider_name, exc)
        return Response(
            {"detail": f"Could not initiate payment via {provider_name}: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )


def _extract_client_ip(request) -> str:
    """Best-effort extraction of the real client IP (X-Forwarded-For first)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _create_mirpay_payment(order):
    """Initiate a MirPay payment for the given order."""
    return mirpay.create_payment(order)


def _create_inpay_payment(order, client_ip: str = ""):
    """Initiate an inPAY payment for the given order."""
    client = InPayClient()
    return client.create_payment(order, client_ip=client_ip)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_status(request, pk):
    """
    GET /api/orders/{id}/status/
    Returns the current order status — used by the frontend to poll for payment confirmation.
    """
    order = get_object_or_404(Order, pk=pk, buyer=request.user)
    return Response(
        {
            "id": order.id,
            "status": order.status,
            "price": str(order.price_at_purchase),
            "project_title": order.project.title,
            "project_slug": order.project.slug,
            "created_at": order.created_at,
            "paid_at": order.paid_at,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_purchases(request):
    """
    GET /api/orders/my-purchases/
    Returns all paid orders for the current user with project details.
    """
    orders = (
        Order.objects.filter(buyer=request.user, status=Order.Status.PAID)
        .select_related("project")
        .order_by("-paid_at")
    )
    results = []
    for o in orders:
        snapshot = ProjectSnapshot.objects.filter(project=o.project).order_by("-version").first()
        results.append(
            {
                "id": o.id,
                "project_id": o.project_id,
                "title": o.project.title,
                "slug": o.project.slug,
                "description": o.project.description,
                "price": str(o.price_at_purchase),
                "cover_image": o.project.cover_image,
                "tech_stack": o.project.tech_stack,
                "license_type": o.project.license_type,
                "version": snapshot.version if snapshot else None,
                "paid_at": o.paid_at,
            }
        )
    return Response(results)


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
        return Response(
            {
                "detail": (
                    f"Order is {order.get_status_display()}. Only paid orders can be downloaded."
                )
            },
            status=400,
        )

    snapshot = ProjectSnapshot.objects.filter(project=order.project).order_by("-version").first()

    if not snapshot or not snapshot.archive:
        raise Http404("No snapshot archive available for this project.")

    Project.objects.filter(pk=order.project_id).update(download_count=F("download_count") + 1)
    Order.objects.filter(pk=order.pk).update(downloaded_at=timezone.now())

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
