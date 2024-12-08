from .serializers import SendOtpSerializer, VerifyOtpSerializer, SetUpProfileSerializer
from rest_framework import status
from rest_framework.response import Response
from authentication.models import *
from rest_framework.views import APIView
import random
from rest_framework import permissions
from django.conf import settings
from twilio.rest import Client
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt import tokens


class SendOTPAPIView(APIView):

    http_method_names = ["post"]

    def get_object(self, phone_number):
        try:
            return UserModel.objects.get(phone_number=phone_number)
        except UserModel.DoesNotExist:
            return None

    def generate_otp(self, phone_number):
        otp_code = random.randint(100000, 999999)
        # Update the existing record or create a new one
        OtpModel.objects.update_or_create(
            phone_number=phone_number,  # Lookup field
            defaults={
                "otp_code": str(otp_code),
                "otp_status": str(OtpStatusChoices.NEW),
            },  # Fields to update or set
        )
        return otp_code

    def post(self, request, *args, **kwargs):
        serializer = SendOtpSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data["phone_number"]

        user = self.get_object(phone_number)

        if user:
            UserModel.objects.filter(phone_number=phone_number).update(
                is_phone_verified=False
            )

        otp_code = self.generate_otp(phone_number=phone_number)

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        try:
            client.messages.create(
                body=f"Your SriSu Verification Code is {otp_code}",
                from_=settings.TWILIO_PHONE_NUMBER,
                to=phone_number,
            )

            return Response(
                {"message": "OTP sent Successfully."}, status=status.HTTP_200_OK
            )
        except Exception as exception:
            print(f"SEND OTP EXCEPTION -- {str(exception)}")
            return Response(
                {"error": str(exception)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VerifyOTPAPIView(APIView):
    http_method_names = ["post"]

    def generate_tokens(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    def revoke_existing_tokens(self, user):

        try:
            # Find all outstanding tokens for the user
            is_deleted = tokens.objects.filter(user=user).delete()
            
            print(f'IS TOKEN DELETED = {is_deleted}')

        except Exception as e:
            print(f"Error finding outstanding tokens: {e}")

    def post(self, request, *args, **kwargs):
        serializer = VerifyOtpSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data["phone_number"]

        # Update user phone verification status
        user, created = UserModel.objects.update_or_create(
            phone_number=phone_number,
            defaults={"is_phone_verified": True},
        )

        self.revoke_existing_tokens(user=user)

        # Update the otp model as otp expired as after the otp is used, it is expired.
        OtpModel.objects.filter(phone_number=phone_number).update(
            otp_status=OtpStatusChoices.EXPIRED
        )

        tokens = self.generate_tokens(user=user)
        response_data = {
            "user": {
                "id": user.id,
                "phone_number": user.phone_number,
                "is_phone_verified": user.is_phone_verified,
            },
            "tokens": tokens,
        }

        return Response(
            {"message": "Phone number verified successfully.", "data": response_data},
            status=status.HTTP_200_OK,
        )


class SetUpProfileAPIView(APIView):

    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, phone_number):
        try:
            return UserModel.objects.get(phone_number=phone_number)
        except UserModel.DoesNotExist:
            return None

    def post(self, request, *args, **kwargs):

        phone_number = request.data.get("phone_number")
        if not phone_number:
            return Response(
                {"error": "Phone number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = self.get_object(phone_number)
        if not user:
            return Response(
                {"error": "User with this phone number does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        elif not user.is_phone_verified:
            return Response(
                {"error": "Phone number is not verified yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SetUpProfileSerializer(
            user, data=request.data, partial=True
        )  # Partial update to allow updates only for provided fields
        if serializer.is_valid():
            serializer.save(is_profile_complete=True)
            return Response(
                {
                    "user": serializer.data,
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
