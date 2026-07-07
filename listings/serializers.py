from rest_framework import serializers
from .models import Category, Project


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class PublicProjectSerializer(serializers.ModelSerializer):
    """Serializer for public project catalog - hides private repo details."""

    seller_profile = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "title",
            "slug",
            "description",
            "price",
            "tags",
            "cover_image",
            "screenshots",
            "demo_url",
            "tech_stack",
            "category_name",
            "seller_profile",
            "view_count",
            "created_at",
        )
        read_only_fields = fields

    def get_seller_profile(self, obj):
        """Return public seller profile info."""
        return {
            "username": obj.seller.username,
            "avatar_url": obj.seller.avatar_url or "",
            "bio": obj.seller.bio,
        }


class ProjectSerializer(serializers.ModelSerializer):
    seller_username = serializers.CharField(source="seller.username", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "seller",
            "seller_username",
            "category",
            "category_name",
            "title",
            "slug",
            "description",
            "github_repo_full_name",
            "github_default_branch",
            "price",
            "tags",
            "cover_image",
            "screenshots",
            "demo_url",
            "tech_stack",
            "license_type",
            "status",
            "version",
            "view_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "seller", "seller_username", "slug", "status", "version", "view_count", "created_at", "updated_at")
