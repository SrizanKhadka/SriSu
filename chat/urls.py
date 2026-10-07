from django.urls import path

from chat.api.views import MediaUploadView
from chat.api.history import RoomHistoryView, RoomListView
from chat.api.v2 import (
    CapabilitiesView,
    RoomAttachmentDetailView,
    RoomAttachmentsView,
    RoomChangesView,
    RoomMessagesView,
    RoomOperationsView,
    RoomReceiptsView,
)

urlpatterns = [
    path("v2/capabilities/", CapabilitiesView.as_view(), name="chat-v2-capabilities"),
    path("v2/rooms/<uuid:room_id>/messages/", RoomMessagesView.as_view(), name="chat-v2-messages"),
    path("v2/rooms/<uuid:room_id>/changes/", RoomChangesView.as_view(), name="chat-v2-changes"),
    path("v2/rooms/<uuid:room_id>/operations/", RoomOperationsView.as_view(), name="chat-v2-operations"),
    path("v2/rooms/<uuid:room_id>/receipts/", RoomReceiptsView.as_view(), name="chat-v2-receipts"),
    path("v2/rooms/<uuid:room_id>/attachments/", RoomAttachmentsView.as_view(), name="chat-v2-attachments"),
    path(
        "v2/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/",
        RoomAttachmentDetailView.as_view(),
        name="chat-v2-attachment",
    ),
    path("rooms/", RoomListView.as_view(), name="chat-room-list"),
    path("rooms/<uuid:room_id>/messages/", RoomHistoryView.as_view(), name="chat-room-history"),
    path("media-upload/", MediaUploadView.as_view(), name="media-upload"),
]
