
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

from social.api.moment_views import MomentNoteView
from social.api.couple_views import CoupleFaveDetail, CoupleFaveList, CoupleFeedView, CoupleMomentSequence

social_routers = DefaultRouter()
social_routers.register("moment-notes", MomentNoteView, basename="moment_notes")

social_routers.register("connect-couple", CoupleConnectionView, basename="coupleConnectionView")
social_routers.register("update-couple", CoupleAPIView, basename="updateCoupleView")
social_routers.register("couple-connection", CoupleConnectionRequestView, basename="coupleConnectionRequestView")
social_routers.register("connect-single", SingleConnectionView, basename="singleConnectionView")
social_routers.register("single-connection", SingleConnectionRequestView, basename="singleConnectionRequestView")
social_routers.register("user-preferences", UserPreferenceView, basename="user_preferences")
social_routers.register("couple-moments", CoupleMomentView, basename="couple_moments")

from social.api.couple_profile_views import (ProfileDetail, ProfileSection, ProfileCover,
    ProfileMemberPhoto, ProfileHistory, ProfileCoverChoices, ProfilePlans, ProfilePlanDetail, ProfileStoryInvite)

urlpatterns = [
    path("profiles/<int:couple_id>/story-invites/", ProfileStoryInvite.as_view(), name="profile-story-invite"),
    path("profiles/me/", ProfileDetail.as_view(), name="profile-self"),
    path("profiles/<int:couple_id>/", ProfileDetail.as_view(), name="profile-detail"),
    path("profiles/<int:couple_id>/sections/<str:section>/", ProfileSection.as_view(), name="profile-section"),
    path("profiles/<int:couple_id>/cover/", ProfileCover.as_view(), name="profile-cover"),
    path("profiles/<int:couple_id>/cover-choices/", ProfileCoverChoices.as_view(), name="profile-cover-choices"),
    path("profiles/<int:couple_id>/members/<int:user_id>/photo/", ProfileMemberPhoto.as_view(), name="profile-member-photo"),
    path("profiles/<int:couple_id>/history/", ProfileHistory.as_view(), name="profile-history"),
    path("profiles/<int:couple_id>/plans/", ProfilePlans.as_view(), name="profile-plans"),
    path("profiles/<int:couple_id>/plans/<int:plan_id>/", ProfilePlanDetail.as_view(), name="profile-plan"),
    path("couple-faves/", CoupleFaveList.as_view(), name="couple-faves"),
    path("couple-faves/<str:couple_id>/", CoupleFaveDetail.as_view(), name="couple-fave-detail"),
    path("couple-feed/", CoupleFeedView.as_view(), name="couple-feed"),
    path("couple-moments/sequence/", CoupleMomentSequence.as_view(), name="couple-moment-sequence"),
    path("couple-profile/", CoupleProfileAPIView.as_view(), name="couple-profile"),
    path("", include(social_routers.urls)),
    path("user-suggestions/", UserSuggestionView.as_view(), name="user_suggestions"),
    path("find-partner/", find_partner, name="find-partner"),
    path("have-couple-connection-requested/", have_couple_connection_requested, name="have-couple-connection-requested"),
    path("get-suggestion-profile/", get_suggestion_profile_by_id, name="get-suggestion-profile"),
    path("is-user-engaged/", is_engaged, name="is-user-engaged"),
]
