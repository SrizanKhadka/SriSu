from rest_framework import serializers
from authentication.models import *
from rest_framework import serializers
from datetime import timedelta
from django.utils.timezone import now
from utils.choices import OtpStatusChoices
from drf_writable_nested import WritableNestedModelSerializer

class UserPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPhotoAlbumModel
        fields = "__all__"

    def validate(self, data):
        validated_data = super().validate(data)
        existing_photos = UserPhotoAlbumModel.objects.filter(
            user=validated_data["user"]
        ).count()

        if existing_photos >= 10:
            raise serializers.ValidationError("You can only upload 10 photos.")
        return validated_data
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
        request = self.context.get('request')
        print(f"Object: {obj}, Profile photo: {obj.profile_photo}, Request: {self.context.get('request')}")

        if obj.profile_photo and request:
            return request.build_absolute_uri(obj.profile_photo.url)
        elif obj.profile_photo:
            return obj.profile_photo.url
        return None

class UserSuggestionSerializer(serializers.ModelSerializer):
    user_interests = UserInterestSerializer(many=True, read_only=True)
    user_photos = UserPhotoSerializer(many=True, read_only=True)
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
            "crushed"
        ]
        


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

        # Check if OTP is expired
        if self.is_otp_expired(updated_time=otp_record.updated_date):
            otp_record.otp_status = OtpStatusChoices.EXPIRED
            otp_record.save()
            raise serializers.ValidationError({"error": "OTP has expired."})
        elif otp_record.otp_status == OtpStatusChoices.EXPIRED:
            raise serializers.ValidationError({"error": "OTP has expired."})

        # Check if the otp code matches.
        if otp_record.otp_code != otp_code:
            raise serializers.ValidationError({"error": "Invalid OTP."})

        return attrs

    def is_otp_expired(self, updated_time):
        otp_lifespan = 5
        return now() > updated_time + timedelta(minutes=otp_lifespan)

class SetUpProfileSerializer(WritableNestedModelSerializer):

    # profile_photo = serializers.SerializerMethodField()
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

    def validate(self, data):
        validated_data = super().validate(data)

        # List of fields that cannot be empty
        required_fields = [
            "phone_number",
            "full_name",
            "username",
            "gender",
            "zodiac_sign",
            "dob",
        ]

        for field in required_fields:
            if field not in data or not data[field]:
                raise serializers.ValidationError(
                    f"{field.replace('_', ' ').capitalize()} cannot be empty."
                )

        return validated_data
    
    def to_representation(self, instance):
        data = super().to_representation(instance)  # Default serialization
        request = self.context.get("request")
        
        # Modify profile_photo to return absolute URL
        if instance.profile_photo and request:
            data['profile_photo'] = request.build_absolute_uri(instance.profile_photo.url)
        else:
            data['profile_photo'] = None
        
        data['user_interests'] = [
        interest for interest in data.get('user_interests', [])
        if not interest.get('removed', False)
    ]

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

    