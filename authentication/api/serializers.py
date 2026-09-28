from datetime import timedelta

from django.utils.timezone import now
from drf_writable_nested import WritableNestedModelSerializer
from rest_framework import serializers

from authentication.models import (
    InterestCategory,
    InterestModel,
    OtpModel,
    UserInterestModel,
    UserModel,
    UserPhotoAlbumModel,
)
from utils.choices import OtpStatusChoices
from utils.helpers import get_base_url


class UserPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPhotoAlbumModel
        fields = "__all__"

    def validate(self, attrs):
        attrs = super().validate(attrs)

        user = attrs.get("user") or getattr(self.instance, "user", None)
        if not user:
            return attrs

        existing_photos_count = UserPhotoAlbumModel.objects.filter(
            user=user,
            removed=False,
        ).exclude(id=getattr(self.instance, "id", None)).count()

        is_new_photo = self.instance is None and attrs.get("photo")
        if is_new_photo and existing_photos_count >= 10:
            raise serializers.ValidationError("You can only upload 10 photos.")

        return attrs


class UserInterestSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserInterestModel
        fields = "__all__"


class UserModelSerializer(serializers.ModelSerializer):
    user_interests = UserInterestSerializer(many=True, read_only=True)
    user_photos = UserPhotoSerializer(many=True, read_only=True)
    profile_photo = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        exclude = ["password", "last_login", "is_superuser", "is_staff", "is_active", "date_joined", "groups", "user_permissions","first_name", "last_name"]

    def get_profile_photo(self, obj):
        request = self.context.get("request")
        if obj.profile_photo and request:
            return request.build_absolute_uri(obj.profile_photo.url)

        if obj.profile_photo:
            base_url = get_base_url(self.context.get("scope", {}))
            return f"{base_url}{obj.profile_photo.url}"

        return None


class UserSuggestionSerializer(serializers.ModelSerializer):
    user_interests = UserInterestSerializer(many=True, read_only=True)
    user_photos = UserPhotoSerializer(many=True, read_only=True)
    profile_photo = serializers.SerializerMethodField()
    crushed = serializers.BooleanField()

    class Meta:
        model = UserModel
        fields = [
            "id",
            "phone_number",
            "full_name",
            "username",
            "user_interests",
            "user_photos",
            "profile_photo",
            "city",
            "country",
            "dob",
            "gender",
            "zodiac_sign",
            "mood",
            "bio",
            "crushed",
        ]

    def get_profile_photo(self, obj):
        request = self.context.get("request")
        if obj.profile_photo and request:
            return request.build_absolute_uri(obj.profile_photo.url)

        if obj.profile_photo:
            base_url = get_base_url(self.context.get("scope", {}))
            return f"{base_url}{obj.profile_photo.url}"

        return None


def normalize_phone(value):
    import re
    value = re.sub(r"[ ()\-]", "", value.strip())
    if not re.fullmatch(r"\+[1-9][0-9]{7,13}", value):
        raise serializers.ValidationError("Use an international phone number.", code="invalid")
    return value


class SendOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=40)
    request_id = serializers.UUIDField(required=False)

    def validate_phone_number(self, value):
        return normalize_phone(value)


class VerifyOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=40)
    otp_code = serializers.RegexField(r"^[0-9]{6}$", trim_whitespace=False)
    challenge_id = serializers.UUIDField(required=False)

    def validate_phone_number(self, value):
        return normalize_phone(value)


class SetUpProfileSerializer(WritableNestedModelSerializer):
    skip_photo = serializers.BooleanField(write_only=True, required=False)
    user_photos = UserPhotoSerializer(many=True, required=False)
    user_interests = UserInterestSerializer(many=True, required=False)
    class Meta:
        model = UserModel
        fields = [
            "id",
            "phone_number",
            "profile_photo",
            "full_name",
            "username",
            "gender",
            "zodiac_sign",
            "dob",
            "mood",
            "is_profile_complete",
            "is_phone_verified",
            "user_interests",
            "user_photos",
            "country",
            "city",
            "bio",
            "is_engaged",
            "profile_photo_skipped",
            "skip_photo",
        ]
        read_only_fields = ["id", "phone_number", "is_profile_complete", "is_phone_verified", "profile_photo_skipped", "is_engaged"]

    def validate_full_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Enter your full name.", code="required")
        return value

    def validate_username(self, value):
        # Preserve case and Unicode; do not silently rename existing identities.
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Enter a username.", code="required")
        if UserModel.objects.filter(username=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This username is taken.", code="unique")
        return value

    def validate_profile_photo(self, value):
        if not value:
            raise serializers.ValidationError("Choose an image.", code="invalid_image")
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Choose an image under 5 MB.", code="too_large")
        # ImageField verified decoding; bound dimensions before full pixel decode.
        image = value.image
        if image.width * image.height > 20_000_000 or max(image.size) > 8192:
            raise serializers.ValidationError("Choose a smaller image.", code="too_large")
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise serializers.ValidationError("Use JPEG, PNG or WebP.", code="invalid_image")
        from io import BytesIO
        from uuid import uuid4
        from PIL import Image, ImageOps
        from django.core.files.uploadedfile import SimpleUploadedFile
        value.seek(0)
        with Image.open(value) as source:
            clean = ImageOps.exif_transpose(source).convert("RGB")
            clean.thumbnail((1600, 1600))
            output = BytesIO()
            clean.save(output, format="JPEG", quality=88)
        # Re-encode and assign a server filename. Neither metadata nor filename is trusted.
        return SimpleUploadedFile(f"{uuid4()}.jpg", output.getvalue(), content_type="image/jpeg")

    def validate(self, attrs):
        if attrs.get("skip_photo") and attrs.get("profile_photo"):
            raise serializers.ValidationError({"skip_photo": "Choose upload or skip."})
        return attrs

    def update(self, instance, validated_data):
        skip = validated_data.pop("skip_photo", False)
        if skip:
            validated_data["profile_photo_skipped"] = True
        elif validated_data.get("profile_photo"):
            validated_data["profile_photo_skipped"] = False
        instance = super().update(instance, validated_data)
        complete = bool(instance.is_phone_verified and instance.full_name and instance.username
                        and (instance.profile_photo or instance.profile_photo_skipped))
        # Preserve established completed accounts through the additive migration.
        if complete and not instance.is_profile_complete:
            instance.is_profile_complete = True
            instance.save(update_fields=["is_profile_complete", "updated_date"])
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)

        data["user_interests"] = [
            interest
            for interest in data.get("user_interests", [])
            if not interest.get("removed", False)
        ]

        data["user_photos"] = sorted(
            [
                photo
                for photo in data.get("user_photos", [])
                if not photo.get("removed", False)
            ],
            key=lambda x: x.get("id", 0),
        )

        return data


class InterestCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = InterestCategory
        fields = "__all__"


class InterestSerializer(serializers.ModelSerializer):
    category = InterestCategorySerializer(read_only=True)

    class Meta:
        model = InterestModel
        fields = "__all__"