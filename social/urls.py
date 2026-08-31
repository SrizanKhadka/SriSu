
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from social.api.views import (
    CoupleConnectionRequestView,
    CoupleConnectionView,
    CoupleAPIView,
    CoupleProfileAPIView,
    CoupleMomentView,
    SingleConnectionView,
    SingleConnectionRequestView,
    UserPreferenceView,
    UserSuggestionView,
    find_partner,
    get_suggestion_profile_by_id,
    have_couple_connection_requested,
    is_engaged,
)

social_routers = DefaultRouter()

social_routers.register("connect-couple", CoupleConnectionView, basename="coupleConnectionView")
social_routers.register("update-couple", CoupleAPIView, basename="updateCoupleView")
social_routers.register("couple-connection", CoupleConnectionRequestView, basename="coupleConnectionRequestView")
social_routers.register("connect-single", SingleConnectionView, basename="singleConnectionView")
social_routers.register("single-connection", SingleConnectionRequestView, basename="singleConnectionRequestView")
social_routers.register("user-preferences", UserPreferenceView, basename="user_preferences")
social_routers.register("couple-moments", CoupleMomentView, basename="couple_moments")

urlpatterns = [
    path("couple-profile/", CoupleProfileAPIView.as_view(), name="couple-profile"),
    path("", include(social_routers.urls)),
    path("user-suggestions/", UserSuggestionView.as_view(), name="user_suggestions"),
    path("find-partner/", find_partner, name="find-partner"),
    path("have-couple-connection-requested/", have_couple_connection_requested, name="have-couple-connection-requested"),
    path("get-suggestion-profile/", get_suggestion_profile_by_id, name="get-suggestion-profile"),
    path("is-user-engaged/", is_engaged, name="is-user-engaged"),
]
