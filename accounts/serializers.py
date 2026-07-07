from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "github_id",
            "github_username",
            "avatar_url",
            "bio",
            "is_seller",
            "telegram_chat_id",
            "created_at",
        )
        read_only_fields = (
            "id",
            "username",
            "email",
            "github_id",
            "github_username",
            "is_seller",
            "created_at",
        )
