
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from social.api.views import (
    CoupleConnectionRequestView,
    CoupleConnectionView,
    CoupleAPIView,
    SingleConnectionView,
    SingleConnectionRequestView,
    UserPreferenceView,
    UserSuggestionView,
    find_partner,
    get_suggestion_profile_by_id,
)

social_routers = DefaultRouter()

social_routers.register("connect-couple", CoupleConnectionView, basename="coupleConnectionView")
social_routers.register("update-couple", CoupleAPIView, basename="updateCoupleView")
social_routers.register("couple-connection", CoupleConnectionRequestView, basename="coupleConnectionRequestView")
social_routers.register("connect-single", SingleConnectionView, basename="singleConnectionView")
social_routers.register("single-connection", SingleConnectionRequestView, basename="singleConnectionRequestView")
social_routers.register("user-preferences", UserPreferenceView, basename="user_preferences")

urlpatterns = [
    path("", include(social_routers.urls)),
    path("user-suggestions/", UserSuggestionView.as_view(), name="user_suggestions"),
    path("find-partner/", find_partner, name="find-partner"),
    path("get-suggestion-profile/", get_suggestion_profile_by_id, name="get-suggestion-profile"),
]