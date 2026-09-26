"""Bounded legacy command validation; domain rules remain in chat services."""
import json
from time import monotonic

from django.conf import settings
from rest_framework import serializers

from chat.websocket.actions import ChatSocketActions


class RoomPayload(serializers.Serializer):
    chat_room_id = serializers.UUIDField()


class HistoryPayload(RoomPayload):
    cursor = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    limit = serializers.IntegerField(default=20, min_value=1, max_value=50)


class RoomsPayload(serializers.Serializer):
    limit = serializers.IntegerField(default=20, min_value=1, max_value=50)
    last_updated = serializers.DateTimeField(required=False, allow_null=True)


class SendPayload(RoomPayload):
    text = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=4000)
    message_type = serializers.CharField(default="text", max_length=20)
    media_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=10)
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    media_url = serializers.URLField(required=False, allow_null=True, allow_blank=True, max_length=500)
    sticker_url = serializers.URLField(required=False, allow_null=True, allow_blank=True, max_length=500)


class MessagePayload(serializers.Serializer):
    message_id = serializers.IntegerField(min_value=1)


class EditPayload(MessagePayload):
    text = serializers.CharField(max_length=4000)


class DeletePayload(MessagePayload):
    delete_option = serializers.CharField(max_length=30)


class ReactionPayload(MessagePayload):
    reaction = serializers.CharField(max_length=20)


class TypingPayload(RoomPayload):
    is_typing = serializers.BooleanField()


PAYLOADS = {
    "unsubscribe_room": RoomPayload,
    ChatSocketActions.FETCH_MESSAGES: HistoryPayload,
    ChatSocketActions.GET_CHAT_ROOMS: RoomsPayload,
    ChatSocketActions.SEND_MESSAGE: SendPayload,
    ChatSocketActions.EDIT_MESSAGE: EditPayload,
    ChatSocketActions.DELETE_MESSAGE: DeletePayload,
    ChatSocketActions.MARK_READ: RoomPayload,
    ChatSocketActions.MARK_DELIVERED: RoomPayload,
    ChatSocketActions.REACT_TO_MESSAGE: ReactionPayload,
    ChatSocketActions.SET_TYPING: TypingPayload,
}


class CommandEnvelope(serializers.Serializer):
    action = serializers.CharField(max_length=64)
    request_id = serializers.CharField(required=False, allow_null=True, max_length=128)
    payload = serializers.DictField(default=dict)


def decode_command(text):
    if not isinstance(text, str):
        raise serializers.ValidationError({"frame": "invalid_frame"})
    if len(text.encode()) > settings.WS_MAX_FRAME_BYTES:
        error = serializers.ValidationError({"frame": "payload_too_large"}, code="payload_too_large")
        error.status_code = 413
        raise error
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise serializers.ValidationError({"frame": "invalid_json"})
    envelope = CommandEnvelope(data=data)
    envelope.is_valid(raise_exception=True)
    command = envelope.validated_data
    payload_type = PAYLOADS.get(command["action"])
    if payload_type is None:
        return command
    payload = payload_type(data=command["payload"])
    try:
        payload.is_valid(raise_exception=True)
    except serializers.ValidationError as exc:
        exc.request_id = command.get("request_id")
        exc.action = command["action"]
        raise
    # Keep primitive wire types expected by existing handlers (UUID/datetime as str).
    command["payload"] = json.loads(json.dumps(payload.validated_data, default=str))
    return command


class CommandBudget:
    """Per-connection resource guard, not a distributed identity/OTP abuse limiter."""
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.tokens = float(settings.WS_COMMAND_BURST)
        self.last = clock()

    def allow(self):
        now = self.clock()
        self.tokens = min(settings.WS_COMMAND_BURST, self.tokens + max(0, now - self.last) * settings.WS_COMMANDS_PER_SECOND)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
