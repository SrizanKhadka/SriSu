from .serializers import SendOptSerializer
from rest_framework import status
from rest_framework.response import Response
from authentication.models import *
from rest_framework.viewsets import ModelViewSet
import random
from django.conf import settings
from twilio.rest import Client


class SendOTPAPIView(ModelViewSet):

    queryset = UserModel.objects.all()
    serializer_class = SendOptSerializer

    http_method_names = ["post"]

    def generate_opt(self, phone_number):
        otp_code = random.randint(100000, 999999)
        OtpModel.objects.create(phone_number, otp_code)

        return otp_code

    def create(self, request, *args, **kwargs):
        data = request.data
        phone_number = data["phone_number"]

        otp_code = self.generate_opt(phone_number=phone_number)

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        try:
            client.messages.create(
                body=f"Your SriSu Verification Code is {otp_code}",
                from_="+9779863938267",
                to=phone_number,
            )

            return Response(
                {"message": "OTP sent Successfully."}, status=status.HTTP_200_OK
            )
        except Exception as exception:
            return Response(
                {"error": str(exception)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
