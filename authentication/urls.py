from django.contrib import admin
from django.urls import path, include
from authentication.api.views import *
from rest_framework.routers import DefaultRouter

auth_routers = DefaultRouter()

auth_routers.register("user-preferences", UserPreferenceView, basename="user_preferences")
auth_routers.register("user-suggestions", UserSuggestionView, basename="user_suggestions")

urlpatterns = [
    path("", include(auth_routers.urls)),
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path("setup-profile/", SetUpProfileAPIView.as_view(), name="setup-profile")
]