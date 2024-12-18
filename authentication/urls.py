from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from authentication.api.views import *


routers = DefaultRouter()

urlpatterns = [
    path("", include(routers.urls)),
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path("setup-profile/", SetUpProfileAPIView.as_view(), name="setup-profile")
]