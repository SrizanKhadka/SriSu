
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from chat.api.views import *


chat_routers = DefaultRouter()

chat_routers.register("media-upload",MediaUploadView,basename="mediaUploadView")

urlpatterns = [
    path("",include(chat_routers.urls))
]