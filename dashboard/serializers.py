from rest_framework import serializers

from orders.models import Order
from listings.models import Project


class DashboardOrderSerializer(serializers.ModelSerializer):
    buyer_username = serializers.CharField(source="buyer.username", read_only=True)
    project_title = serializers.CharField(source="project.title", read_only=True)
    project_slug = serializers.SlugField(source="project.slug", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "buyer_username", "project_title", "project_slug",
            "price_at_purchase", "platform_fee_amount", "seller_earning_amount",
            "status", "created_at", "paid_at",
        ]


class DashboardProjectSerializer(serializers.ModelSerializer):
    sales_count = serializers.IntegerField(read_only=True)
    revenue = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Project
        fields = [
            "id", "title", "slug", "price", "status",
            "view_count", "download_count", "sales_count", "revenue",
            "created_at", "updated_at",
        ]


class EarningsEntrySerializer(serializers.Serializer):
    date = serializers.DateField()
    earnings = serializers.DecimalField(max_digits=12, decimal_places=2)
