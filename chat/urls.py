from django.urls import path

from chat.api.views import MediaUploadView
from chat.api.history import RoomHistoryView, RoomListView

urlpatterns = [
    path("rooms/", RoomListView.as_view(), name="chat-room-list"),
    path("rooms/<uuid:room_id>/messages/", RoomHistoryView.as_view(), name="chat-room-history"),
    path("media-upload/", MediaUploadView.as_view(), name="media-upload"),
]
