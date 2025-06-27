
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from social.api.views import *


social_routers = DefaultRouter()

social_routers.register( "connect-couple", CoupleConnectionView, basename="coupleConnectionView")
social_routers.register("update-couple", CoupleAPIView, basename="updateCoupleView")
social_routers.register("couple-connection", CoupleConnectionRequestView, basename="coupleConnectionRequestView")
social_routers.register("connect-single", SingleConnectionView, basename="singleConnectionView")
social_routers.register("single-connection", SingleConnectionRequestView, basename="singleConnectionRequestView")
social_routers.register("user-preferences", UserPreferenceView, basename="user_preferences")
social_routers.register("user-suggestions", UserSuggestionView, basename="user_suggestions")

urlpatterns = [
    path("",include(social_routers.urls))
]