from .serializers import SendOtpSerializer, VerifyOtpSerializer, SetUpProfileSerializer
from rest_framework import status
from rest_framework.response import Response
from authentication.models import *
from rest_framework.views import APIView
import random
from django.conf import settings
from twilio.rest import Client
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.tokens import RefreshToken


class SendOTPAPIView(APIView):

    http_method_names = ["post"]

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

    def post(self, request, *args, **kwargs):
        serializer = VerifyOtpSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data["phone_number"]

        # Update user phone verification status
        UserModel.objects.update_or_create(
            phone_number=phone_number,
            defaults={"is_phone_verified": True},
        )

        OtpModel.objects.filter(phone_number=phone_number).update(
            otp_status=OtpStatusChoices.EXPIRED
        )

        return Response(
            {"message": "Phone number verified successfully."},
            status=status.HTTP_200_OK,
        )


class SetUpProfileAPIView(ModelViewSet):
    queryset = UserModel.objects.all()
    serializer_class = SetUpProfileSerializer

    http_method_names = ["post"]

    def get_queryset(self):
        phone_number = self.request.data.get("phone_number")
        if phone_number:
            return self.queryset.filter(phone_number=phone_number)
        return self.queryset.none()

    def perform_update(self, serializer):
        serializer.save(is_profile_complete=True)

    def generate_tokens(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    def update(self, request, *args, **kwargs):
        phone_number = request.data.get("phone_number")
        if not phone_number:
            return Response(
                {"error": "Phone number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if the user exists
        try:
            user = UserModel.objects.get(phone_number=phone_number)
        except UserModel.DoesNotExist:
            return Response(
                {"error": "User with this phone number does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Ensure the current object matches the requested phone number
        self.kwargs["pk"] = user.id

        # Perform the profile update
        response = super().update(request, *args, **kwargs)

        # Generate tokens after profile setup
        tokens = self.generate_tokens(user)
        response.data = {
            "user": self.get_serializer(user).data,
            "tokens": tokens,
        }
        return response
