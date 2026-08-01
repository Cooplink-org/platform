from decimal import Decimal

from rest_framework import serializers

from .models import Category, Comment, Project, ProjectQA, Rating


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class PublicProjectSerializer(serializers.ModelSerializer):
    """Serializer for public project detail page — includes full description and screenshots."""

    seller_profile = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "title",
            "slug",
            "description",
            "long_description",
            "price",
            "tags",
            "cover_image",
            "banner_image",
            "accent_color",
            "highlights",
            "screenshots",
            "demo_url",
            "tech_stack",
            "license_type",
            "category_name",
            "seller_profile",
            "featured",
            "view_count",
            "download_count",
            "average_rating",
            "rating_count",
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


class PublicProjectListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for the public project listing page.
    Excludes description, screenshots, and other large fields that are
    only needed on the detail page. Reduces response size significantly.
    """

    seller_username = serializers.CharField(source="seller.username", read_only=True)
    seller_avatar = serializers.CharField(source="seller.avatar_url", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "title",
            "slug",
            "price",
            "tags",
            "cover_image",
            "tech_stack",
            "category_name",
            "seller_username",
            "seller_avatar",
            "featured",
            "view_count",
            "average_rating",
            "rating_count",
            "created_at",
        )
        read_only_fields = fields


class ProjectSerializer(serializers.ModelSerializer):
    seller_username = serializers.CharField(source="seller.username", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)
    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("1.00"),
    )

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
            "long_description",
            "github_repo_full_name",
            "github_default_branch",
            "price",
            "tags",
            "cover_image",
            "banner_image",
            "accent_color",
            "highlights",
            "featured",
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
        read_only_fields = (
            "id",
            "seller",
            "seller_username",
            "slug",
            "status",
            "version",
            "view_count",
            "featured",
            "created_at",
            "updated_at",
        )

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Title cannot be blank.")
        return value.strip()

    def validate_description(self, value):
        if not value.strip():
            raise serializers.ValidationError("Description cannot be blank.")
        if len(value) > 50000:
            raise serializers.ValidationError("Description must be under 50,000 characters.")
        return value

    def validate_tags(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Tags must be a list of strings.")
        if len(value) > 20:
            raise serializers.ValidationError("Maximum 20 tags allowed.")
        for tag in value:
            if not isinstance(tag, str) or not tag.strip():
                raise serializers.ValidationError("Each tag must be a non-empty string.")
        return [t.strip() for t in value]

    def validate_accent_color(self, value):
        import re

        if value and not re.match(r"^#[0-9a-fA-F]{3,8}$", value):
            raise serializers.ValidationError(
                "Accent color must be a valid hex color (e.g. #3fd68c)."
            )
        return value


class CommentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    avatar_url = serializers.CharField(source="user.avatar_url", read_only=True)

    class Meta:
        model = Comment
        fields = (
            "id",
            "project",
            "user",
            "username",
            "avatar_url",
            "body",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "project",
            "user",
            "username",
            "avatar_url",
            "created_at",
            "updated_at",
        )

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Comment body cannot be blank.")
        if len(value) > 5000:
            raise serializers.ValidationError("Comment must be under 5,000 characters.")
        return value.strip()


class RatingSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Rating
        fields = (
            "id",
            "project",
            "user",
            "username",
            "score",
            "review_text",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "project", "user", "username", "created_at", "updated_at")

    def validate_score(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("Score must be between 1 and 5.")
        return value

    def validate_review_text(self, value):
        if value and len(value) > 5000:
            raise serializers.ValidationError("Review text must be under 5,000 characters.")
        return value


class ProjectQASerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = ProjectQA
        fields = (
            "id",
            "project",
            "user",
            "author",
            "question",
            "answer",
            "created_at",
            "answered_at",
        )
        read_only_fields = ("id", "project", "user", "author", "created_at", "answered_at")

    def get_author(self, obj):
        return {
            "id": obj.user.id,
            "username": obj.user.username,
            "avatar_url": getattr(obj.user, "avatar_url", "") or "",
        }

    def validate_question(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Question cannot be blank.")
        if len(value) > 2000:
            raise serializers.ValidationError("Question must be under 2,000 characters.")
        return value.strip()
