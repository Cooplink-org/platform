from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    is_onboarded = serializers.BooleanField(read_only=True)

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
            "full_legal_name",
            "phone_number",
            "phone_verified",
            "phone_verified_at",
            "terms_accepted_version",
            "terms_accepted_at",
            "is_onboarded",
            "is_staff",
            "created_at",
        )
        read_only_fields = (
            "id",
            "username",
            "email",
            "github_id",
            "github_username",
            "is_seller",
            "is_staff",
            "is_onboarded",
            "phone_verified",
            "phone_verified_at",
            "terms_accepted_version",
            "terms_accepted_at",
            "telegram_chat_id",
            "created_at",
        )


class OnboardingSerializer(serializers.ModelSerializer):
    terms_accepted = serializers.BooleanField(write_only=True, required=True)

    class Meta:
        model = User
        fields = (
            "full_legal_name",
            "phone_number",
            "avatar_url",
            "terms_accepted",
        )

    def validate_full_legal_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Full legal name is required.")
        return value.strip()

    def validate_phone_number(self, value):
        if not value.strip():
            raise serializers.ValidationError("Phone number is required.")
        # Run the model's RegexValidator
        from django.core.validators import RegexValidator

        validator = RegexValidator(
            regex=r"^\+?1?\d{7,15}$",
            message="Phone number must be 7-15 digits, optionally starting with '+' or '1'.",
        )
        validator(value)
        return value.strip()

    def validate_terms_accepted(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must accept the Terms of Use and Privacy Policy."
            )
        return value

    def validate_avatar_url(self, value):
        if value and not value.strip():
            return None
        return value
