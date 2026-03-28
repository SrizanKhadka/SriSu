import random
from datetime import timedelta

from django.conf import settings
from django.utils.timezone import now
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from twilio.rest import Client

from authentication.api.serializers import (
    InterestSerializer,
    SendOtpSerializer,
    SetUpProfileSerializer,
    UserModelSerializer,
    VerifyOtpSerializer,
)
from authentication.models import InterestModel, OtpModel, UserInterestModel, UserModel, UserPhotoAlbumModel
from utils.choices import OtpStatusChoices


class SendOTPAPIView(APIView):
    http_method_names = ["post"]

    @staticmethod
    def get_user_by_phone_number(phone_number):
        return UserModel.objects.filter(phone_number=phone_number).first()

    @staticmethod
    def generate_otp(phone_number):
        otp_code = random.randint(100000, 999999)

        OtpModel.objects.update_or_create(
            phone_number=phone_number,
            defaults={
                "otp_code": str(otp_code),
                "otp_status": OtpStatusChoices.NEW,
                "last_request_time": now(),
            },
        )
        return otp_code

    @staticmethod
    def update_otp_attempts(phone_number):
        otp_record = OtpModel.objects.filter(phone_number=phone_number).first()
        if otp_record:
            otp_record.otp_attempts += 1
            otp_record.save(update_fields=["otp_attempts", "updated_date"])

    def unverify_user(self, phone_number):
        user = self.get_user_by_phone_number(phone_number)
        if user and user.is_phone_verified:
            user.is_phone_verified = False
            user.save(update_fields=["is_phone_verified", "updated_date"])

    def send_otp_sms(self, phone_number, otp_code):
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        try:
            client.messages.create(
                body=f"Your SriSu Verification Code is {otp_code}",
                from_=settings.TWILIO_PHONE_NUMBER,
                to=phone_number,
            )
            self.update_otp_attempts(phone_number)
            return True
        except Exception:
            return False

    @staticmethod
    def can_request_otp(phone_number):
        otp_record = OtpModel.objects.filter(phone_number=phone_number).first()

        if not otp_record:
            return True

        now_time = now()
        time_diff = now_time - otp_record.last_request_time

        if time_diff > timedelta(minutes=10):
            otp_record.otp_attempts = 0
            otp_record.last_request_time = now_time
            otp_record.save(update_fields=["otp_attempts", "last_request_time", "updated_date"])
            return True

        return otp_record.otp_attempts < 3

    def post(self, request, *args, **kwargs):
        serializer = SendOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        self.unverify_user(phone_number)

        if not self.can_request_otp(phone_number):
            return Response(
                {"error": "Too many OTP requests. Try again in few minutes."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = self.generate_otp(phone_number)
        success = self.send_otp_sms(phone_number, otp_code)

        if success:
            return Response(
                {"message": "OTP sent successfully."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": "Failed to send OTP."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class VerifyOTPAPIView(APIView):
    http_method_names = ["post"]

    @staticmethod
    def generate_tokens(user):
        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    def post(self, request, *args, **kwargs):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        OtpModel.objects.filter(phone_number=phone_number).update(
            otp_status=OtpStatusChoices.EXPIRED
        )

        user, _ = UserModel.objects.update_or_create(
            phone_number=phone_number,
            defaults={"is_phone_verified": True},
        )

        response_data = self.generate_tokens(user=user)
        user_data = UserModelSerializer(user, context={"request": request}).data

        return Response(
            {
                "message": "Phone number verified successfully.",
                "data": {
                    "user": user_data,
                    "tokens": response_data,
                },
            },
            status=status.HTTP_200_OK,
        )


class SetUpProfileAPIView(APIView):
    http_method_names = ["get", "put", "patch"]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def get_user_by_phone_number(phone_number):
        return UserModel.objects.filter(phone_number=phone_number).first()

    def get(self, request, *args, **kwargs):
        user = request.user
        if not user or not user.is_authenticated:
            raise ValidationError({"error": "User does not exist."})

        serializer = SetUpProfileSerializer(user, context={"request": request})
        return Response(
            {
                "message": "User profile retrieved successfully",
                "data": {"user": serializer.data},
            },
            status=status.HTTP_200_OK,
        )

    def put(self, request, *args, **kwargs):
        return self.update(request)

    def patch(self, request, *args, **kwargs):
        return self.update(request)

    def update(self, request):
        if not request.user or not request.user.is_authenticated:
            raise ValidationError({"error": "Authentication required."})

        payload = request.data.copy()
        phone_number = payload.get("phone_number") or request.user.phone_number
    
        if not phone_number:
            raise ValidationError({"error": "Phone number is required."})

        user = self.get_user_by_phone_number(phone_number)
        if not user:
            raise ValidationError({"error": "User with this phone number does not exist."})

        if request.user.id != user.id:
            raise ValidationError({"error": "You are not allowed to update this profile."})

        if not user.is_phone_verified:
            raise ValidationError({"error": "Phone number is not verified yet."})

        user_interests_data = payload.pop("user_interests", [])
        self.manage_user_interests(user, user_interests_data)

        user_photos_data = payload.pop("user_photos", [])
        self.manage_user_photos(user, user_photos_data)
        
        print("Payload after popping nested data:", payload)

        serializer = SetUpProfileSerializer(
            user,
            data=payload,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(is_profile_complete=True)

        return Response(
            {
                "message": "Profile updated successfully",
                "data": {"user": serializer.data},
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def manage_user_photos(user, user_photos_data):
        for photo_data in user_photos_data:
            photo_id = photo_data.get("id")
            removed = photo_data.get("removed", False)
            new_photo = photo_data.get("photo")

            if not photo_id:
                # Case 1: New photo upload
                if new_photo:
                    UserPhotoAlbumModel.objects.create(
                        user=user,
                        photo=new_photo,
                        removed=False,
                    )
                continue

            try:
                photo_instance = UserPhotoAlbumModel.objects.get(id=photo_id, user=user)
            except UserPhotoAlbumModel.DoesNotExist:
                raise ValidationError({"error": f"Photo with id {photo_id} not found."})

            if removed:
                #Case 2: Mark as removed
                photo_instance.removed = True
                photo_instance.save(update_fields=["removed", "updated_date"])
                continue

            if new_photo:
                #Case 3:: Updating the photo
                #Store the old photo in the album befoore reloacing
                UserPhotoAlbumModel.objects.create(
                    user=user,
                    photo=photo_instance.photo,
                    removed=True,
                )
                #Update the instance with the new photo
                photo_instance.photo = new_photo
                photo_instance.created_date = now()
                photo_instance.save(update_fields=["photo", "created_date", "updated_date"])

    @staticmethod
    def manage_user_interests(user, user_interests_data):
        for interest_data in user_interests_data:
            name = interest_data.get("name")
            interest_id = interest_data.get("interest")
            removed = interest_data.get("removed", False)

            if not name:
                continue

            interest_qs = UserInterestModel.objects.filter(user=user, name=name)

            if not interest_qs.exists():
                UserInterestModel.objects.create(
                    user=user,
                    name=name,
                    interest_id=interest_id,
                    removed=removed,
                )
                continue

            if removed:
                interest_qs.update(removed=True)
            elif interest_qs.filter(removed=True).exists():
                interest_qs.update(removed=False, interest_id=interest_id)


class InterestsAPIView(APIView):
    http_method_names = ["get"]
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        interests = InterestModel.objects.all()
        serializer = InterestSerializer(interests, many=True)
        return Response(
            {
                "message": "User interests retrieved successfully.",
                "data": {"interests": serializer.data},
            },
            status=status.HTTP_200_OK,
        )