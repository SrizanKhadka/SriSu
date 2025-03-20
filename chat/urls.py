
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from chat.api.views import *


chat_routers = DefaultRouter()

chat_routers.register( "connect-couple", CoupleConnectionView, basename="coupleConnectionView")
chat_routers.register("update-couple", CoupleAPIView, basename="updateCoupleView")
chat_routers.register("media-upload",MediaUploadView,basename="mediaUploadView")
chat_routers.register("couple-connection", CoupleConnectionRequestView, basename="coupleConnectionRequestView")
chat_routers.register("connect-single", SingleConnectionView, basename="singleConnectionView")
chat_routers.register("single-connection", SingleConnectionRequestView, basename="singleConnectionRequestView")

urlpatterns = [
    path("",include(chat_routers.urls))
]