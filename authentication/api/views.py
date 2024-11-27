from .serializers import SendOptSerializer
from rest_framework import status
from rest_framework.response import Response
from authentication.models import *
from rest_framework.viewsets import ModelViewSet
import random
from rest_framework.views import APIView
from datetime import timedelta
from django.utils.timezone import now
from django.conf import settings
from twilio.rest import Client


class SendOTPAPIView(ModelViewSet):

    queryset = UserModel.objects.all()
    serializer_class = SendOptSerializer

    http_method_names = ["post"]

    def generate_opt(self, phone_number):
        otp_code = random.randint(100000, 999999)
        # Update the existing record or create a new one
        OtpModel.objects.update_or_create(
            phone_number=phone_number,  # Lookup field
            defaults={"otp_code": str(otp_code)},  # Fields to update or set
        )
        return otp_code

    def create(self, request, *args, **kwargs):
        data = request.data
        phone_number = data["phone_number"]

        otp_code = self.generate_opt(phone_number=phone_number)

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        try:
            client.messages.create(
                body=f"Your SriSu Verification Code is {otp_code}",
                from_="+16812286983",
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


class VerifyOtpApiView(APIView):

    def post(self, request, *args, **kwargs):
        data = request.data
        phone_number = data["phone_number"]
        otp_code = data["otp_code"]

        if not phone_number or not otp_code:
            return Response(
                {"error": "Both phone_number and otp_code are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            otp_record = OtpModel.objects.get(phone_number=phone_number)
        except OtpModel.DoesNotExist:
            return Response(
                {"error": "Invalid phone number or OTP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if otp_record.otp_code != otp_code:
            return Response(
                {"error": "Invalid OTP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if OTP is expired (5 minutes expiry)
        otp_lifespan = 5  # In minutes
        if now() > otp_record.updated_date + timedelta(minutes=otp_lifespan):
            return Response(
                {"error": "OTP has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = UserModel.objects.create(
            phone_number=phone_number,
            is_phone_verified = True
        )
        
        user.save()

        return Response(
            {"message": "Phone number successfully verified."},
            status=status.HTTP_200_OK,
        )
