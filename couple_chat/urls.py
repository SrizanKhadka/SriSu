from django.urls import path
from .api import ClaimView, DeviceView, KeysView, OperationsView, ParticipantDeviceView, RoomView, StateView, SyncView
from .media_api import AttachmentInitView, AttachmentUploadView, AttachmentFinalizeView, AttachmentCancelView, AttachmentCapabilityView, AttachmentDownloadView
from .sparks import SparkView, SparkAnswerView

urlpatterns = [
    path("v1/rooms/<uuid:room_id>/sparks/<uuid:spark_id>/", SparkView.as_view()),
    path("v1/rooms/<uuid:room_id>/sparks/<uuid:spark_id>/answer/", SparkAnswerView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/", AttachmentInitView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/upload/", AttachmentUploadView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/finalize/", AttachmentFinalizeView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/cancel/", AttachmentCancelView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/capability/", AttachmentCapabilityView.as_view()),
    path("v1/rooms/<uuid:room_id>/attachments/<uuid:attachment_id>/content/", AttachmentDownloadView.as_view()),
    path("v1/state/", StateView.as_view(), name="couple-chat-state"),
    path("v1/rooms/<uuid:room_id>/", RoomView.as_view(), name="couple-chat-room"),
    path("v1/devices/", DeviceView.as_view(), name="couple-chat-devices"),
    path("v1/devices/keys/", KeysView.as_view(), name="couple-chat-keys"),
    path("v1/rooms/<uuid:room_id>/devices/<uuid:device_id>/", ParticipantDeviceView.as_view(), name="couple-chat-participant-device"),
    path("v1/rooms/<uuid:room_id>/bundles/claim/", ClaimView.as_view(), name="couple-chat-bundle-claim"),
    path("v1/rooms/<uuid:room_id>/operations/", OperationsView.as_view(), name="couple-chat-operations"),
    path("v1/rooms/<uuid:room_id>/changes/", SyncView.as_view(), name="couple-chat-changes"),
]
