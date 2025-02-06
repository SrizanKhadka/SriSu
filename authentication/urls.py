from django.contrib import admin
from django.urls import path, include
from authentication.api.views import *


urlpatterns = [
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path("setup-profile/", SetUpProfileAPIView.as_view(), name="setup-profile")
]