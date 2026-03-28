from django.urls import path

from authentication.api.views import (
    InterestsAPIView,
    SendOTPAPIView,
    SetUpProfileAPIView,
    VerifyOTPAPIView,
)

urlpatterns = [
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path("setup-profile/", SetUpProfileAPIView.as_view(), name="setup-profile"),
    path("interests/", InterestsAPIView.as_view(), name="interests"),
]