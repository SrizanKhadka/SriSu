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
        fields = "__all__"

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


class SendOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=15)

    def validate_phone_number(self, value):
        if not value.startswith("+") or len(value) < 10:
            raise serializers.ValidationError("Invalid phone number format.")
        return value


class VerifyOtpSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=15)
    otp_code = serializers.CharField(max_length=6)

    def validate(self, attrs):
        phone_number = attrs["phone_number"]
        otp_code = attrs["otp_code"]

        try:
            otp_record = OtpModel.objects.get(phone_number=phone_number)
        except OtpModel.DoesNotExist:
            raise serializers.ValidationError({"error": "Invalid phone number or OTP."})

        if self.is_otp_expired(otp_record.updated_date):
            otp_record.otp_status = OtpStatusChoices.EXPIRED
            otp_record.save(update_fields=["otp_status", "updated_date"])
            raise serializers.ValidationError({"error": "OTP has expired."})

        if otp_record.otp_status == OtpStatusChoices.EXPIRED:
            raise serializers.ValidationError({"error": "OTP has expired."})

        if otp_record.otp_code != otp_code:
            raise serializers.ValidationError({"error": "Invalid OTP."})

        attrs["otp_record"] = otp_record
        return attrs

    @staticmethod
    def is_otp_expired(updated_time):
        otp_lifespan_minutes = 5
        return now() > updated_time + timedelta(minutes=otp_lifespan_minutes)


class SetUpProfileSerializer(WritableNestedModelSerializer):
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
        ]
        read_only_fields = ["is_profile_complete", "is_phone_verified"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # profile is considered valid only when these are present.
        required_fields = [
            "phone_number",
            "full_name",
            "username",
            "gender",
            "zodiac_sign",
            "dob",
        ]

        instance = getattr(self, "instance", None)

        for field in required_fields:
            incoming_value = attrs.get(field, None)
            existing_value = getattr(instance, field, None) if instance else None

            if not incoming_value and not existing_value:
                raise serializers.ValidationError(
                    {field: f"{field.replace('_', ' ').capitalize()} cannot be empty."}
                )

        return attrs

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