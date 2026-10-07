from __future__ import annotations

from django.conf import settings
from django.http import FileResponse
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from authentication.models import DeviceSession
from chat.api.v2_serializers import (
    ChangePageQuery,
    EncryptedAttachmentUploadSerializer,
    MessageOperationSerializer,
    MessagePageQuery,
    ReceiptSerializer,
    SendEncryptedMessageSerializer,
)
from chat.presenters.v2 import serialize_change_v2, serialize_message_v2
from chat.selectors.access import authorized_rooms
from chat.selectors.chat_room_selectors import get_chat_room_for_user
from chat.services.v2 import (
    ChatV2Conflict,
    ChatV2Error,
    ChatV2NotFound,
    ChatV2Unavailable,
    advance_receipts,
    attachment_for_download,
    cancel_encrypted_attachment,
    message_page,
    mutate_message,
    send_encrypted_message,
    stage_encrypted_attachment,
    visible_changes_window,
)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The resource changed. Refresh before trying again."
    default_code = "conflict"


class EncryptedChatUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Encrypted chat writes are not available."
    default_code = "encrypted_chat_unavailable"
    core_error_code = "encrypted_chat_unavailable"


def _raise_domain(exc: ChatV2Error):
    if isinstance(exc, ChatV2NotFound):
        raise NotFound() from None
    if isinstance(exc, ChatV2Conflict):
        raise Conflict() from None
    if isinstance(exc, ChatV2Unavailable):
        raise EncryptedChatUnavailable() from None
    raise serializers.ValidationError({"non_field_errors": [exc.code]}) from None


def _device_session_id(request):
    auth = getattr(request, "auth", None)
    try:
        return auth.get("sid") if auth else None
    except AttributeError:
        return None


def _private(response):
    response["Cache-Control"] = "private, no-store"
    return response


class V2ThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    read_throttle_scope = "chat_v2_reads"
    write_throttle_scope = "chat_v2_writes"

    def get_throttles(self):
        self.throttle_scope = (
            self.read_throttle_scope
            if self.request.method in ("GET", "HEAD", "OPTIONS")
            else self.write_throttle_scope
        )
        return super().get_throttles()


class CapabilitiesView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        session_ready = True
        if settings.CHAT_V2_REQUIRE_DEVICE_SESSION:
            session_id = _device_session_id(request)
            session_ready = bool(
                session_id
                and DeviceSession.objects.filter(
                    pk=session_id,
                    user=request.user,
                    revoked_at__isnull=True,
                    expires_at__gt=timezone.now(),
                ).exists()
            )
        allowed_user_ids = settings.CHAT_V2_ALLOWED_USER_IDS
        room_ready = authorized_rooms(request.user).filter(
            user_one_id__in=allowed_user_ids,
            user_two_id__in=allowed_user_ids,
        ).exists()
        enabled = (
            settings.CHAT_V2_ENCRYPTED_WRITES_ENABLED
            and settings.CHAT_V2_PROTOCOL_STATUS == "ready"
            and request.user.id in allowed_user_ids
            and room_ready
            and session_ready
        )
        return _private(Response({"data": {
            "api_version": 2,
            "encrypted_writes_enabled": enabled,
            "protocol_status": settings.CHAT_V2_PROTOCOL_STATUS,
            "requires_device_session": settings.CHAT_V2_REQUIRE_DEVICE_SESSION,
            "supported_kinds": ["text"],
            # Client-side plaintext policy only; the server cannot inspect it.
            "max_message_bytes": settings.CHAT_V2_MAX_MESSAGE_PLAINTEXT_BYTES,
            # Authoritative server-enforced size after client sealing.
            "max_envelope_bytes": settings.CHAT_V2_MAX_ENVELOPE_BYTES,
            "max_attachment_bytes": settings.CHAT_V2_MAX_ATTACHMENT_BYTES,
        }}))


class RoomMessagesView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request, room_id):
        query = MessagePageQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        room = get_chat_room_for_user(room_id, request.user)
        if room is None:
            raise NotFound()
        snapshot_sequence = room.last_sequence
        messages, has_more, cursor = message_page(
            room=room,
            user=request.user,
            before_sequence=query.validated_data.get("before_sequence"),
            limit=query.validated_data["limit"],
            max_sequence=snapshot_sequence,
        )
        if get_chat_room_for_user(room_id, request.user) is None:
            raise NotFound()
        return _private(Response({"data": {
            "room_id": str(room.pk),
            "high_watermark": snapshot_sequence,
            "messages": [serialize_message_v2(message, request.user) for message in messages],
            "has_more": has_more,
            "next_before_sequence": cursor,
        }}))

    def post(self, request, room_id):
        serializer = SendEncryptedMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = send_encrypted_message(
                room_id=room_id,
                user=request.user,
                device_session_id=_device_session_id(request),
                **serializer.validated_data,
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        response_status = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
        return _private(Response({"data": {
            "operation_id": str(result.operation.operation_id),
            "replayed": result.replayed,
            "change_sequence": result.change_sequence,
            "message": serialize_message_v2(result.message, request.user),
        }}, status=response_status))


class RoomChangesView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id):
        query = ChangePageQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        room = get_chat_room_for_user(room_id, request.user)
        if room is None:
            raise NotFound()
        snapshot_sequence = room.last_sequence
        changes, has_more, next_after = visible_changes_window(
            room=room,
            user=request.user,
            max_sequence=snapshot_sequence,
            **query.validated_data,
        )
        if get_chat_room_for_user(room_id, request.user) is None:
            raise NotFound()
        return _private(Response({"data": {
            "room_id": str(room.pk),
            "high_watermark": snapshot_sequence,
            "changes": [serialize_change_v2(change, request.user) for change in changes],
            "has_more": has_more,
            # Highest scanned room sequence, including private rows omitted above.
            "next_after_sequence": next_after,
        }}))


class RoomOperationsView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, room_id):
        serializer = MessageOperationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = mutate_message(
                room_id=room_id,
                user=request.user,
                device_session_id=_device_session_id(request),
                **serializer.validated_data,
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        return _private(Response({"data": {
            "operation_id": str(result.operation.operation_id),
            "replayed": result.replayed,
            "change_sequence": result.change_sequence,
            "message": serialize_message_v2(result.message, request.user),
        }}))


class RoomReceiptsView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def put(self, request, room_id):
        serializer = ReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = advance_receipts(
                room_id=room_id,
                user=request.user,
                device_session_id=_device_session_id(request),
                **serializer.validated_data,
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        return _private(Response({"data": {
            "operation_id": str(result.operation.operation_id),
            "replayed": result.replayed,
            "room_id": str(result.receipt.chat_room_id),
            "delivered_through": result.receipt.delivered_through,
            "read_through": result.receipt.read_through,
            "change_sequence": result.change_sequence,
        }}))


class RoomAttachmentsView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    write_throttle_scope = "chat_v2_attachments"

    def post(self, request, room_id):
        serializer = EncryptedAttachmentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            attachment, replayed = stage_encrypted_attachment(
                room_id=room_id,
                user=request.user,
                device_session_id=_device_session_id(request),
                **serializer.validated_data,
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        return _private(Response({"data": {
            "id": str(attachment.pk),
            "operation_id": str(attachment.operation_id),
            "replayed": replayed,
            "state": attachment.state,
            "ciphertext_size": attachment.ciphertext_size,
            "expires_at": attachment.expires_at.isoformat(),
        }}, status=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED))


class RoomAttachmentDetailView(V2ThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    write_throttle_scope = "chat_v2_attachments"

    def get(self, request, room_id, attachment_id):
        try:
            attachment = attachment_for_download(
                room_id=room_id,
                attachment_id=attachment_id,
                user=request.user,
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        response = FileResponse(
            attachment.ciphertext.open("rb"),
            content_type="application/octet-stream",
            as_attachment=True,
            filename=f"{attachment.pk}.bin",
        )
        response["Cache-Control"] = "private, no-store"
        response["Content-Length"] = str(attachment.ciphertext_size)
        return response

    def delete(self, request, room_id, attachment_id):
        try:
            cancel_encrypted_attachment(
                room_id=room_id,
                attachment_id=attachment_id,
                user=request.user,
                device_session_id=_device_session_id(request),
            )
        except ChatV2Error as exc:
            _raise_domain(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)
