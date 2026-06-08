from django.urls import path

from chat.api.views import MediaUploadView

urlpatterns = [
    path("media-upload/", MediaUploadView.as_view(), name="media-upload"),
]