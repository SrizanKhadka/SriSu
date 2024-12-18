from rest_framework import serializers
from authentication.models import OtpModel, UserModel
from rest_framework import serializers
from datetime import timedelta
from django.utils.timezone import now
from utils.choices import OtpStatusChoices
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


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
        print("INSIDE VALIDATE OTP")
        phone_number = attrs["phone_number"]
        otp_code = attrs["otp_code"]

        try:
            otp_record = OtpModel.objects.get(phone_number=phone_number)
        except OtpModel.DoesNotExist:
            raise serializers.ValidationError({"error": "Invalid phone number or OTP."})

            # Check if the otp code matches.
        if otp_record.otp_code != otp_code:
            raise serializers.ValidationError({"error": "Invalid OTP."})

        # Check if OTP is expired
        if self.is_otp_expired(updated_time=otp_record.updated_date):
            otp_record.otp_status = OtpStatusChoices.EXPIRED
            otp_record.save()
            raise serializers.ValidationError({"error": "OTP has expired."})
        elif otp_record.otp_status == OtpStatusChoices.EXPIRED:
            raise serializers.ValidationError({"error": "OTP has expired."})

        return attrs

    def is_otp_expired(self, updated_time):
        otp_lifespan = 5
        return now() > updated_time + timedelta(minutes=otp_lifespan)


class SetUpProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserModel
        fields = [
            "phone_number",
            "profile_photo",
            "full_name",
            "gender",
            "zodiac_sign",
            "dob",
            "mood",
            "is_profile_complete",
            "is_phone_verified"
        ]
        read_only_fields = ["is_profile_complete","is_phone_verified"]

    def validate(self, data):
        validated_data = super().validate(data)

        required_fields = [
            "profile_photo",
            "full_name",
            "gender",
            "zodiac_sign",
            "dob",
            "mood",
        ]

        print(f"PHONE NUMBER = {data.get("phone_number")}")

        for field in required_fields:
            if not data.get(field):
                raise serializers.ValidationError(
                    {field: f"{field} cannot be null or empty."}
                )

        return validated_data
