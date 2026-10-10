from django.conf import settings
from rest_framework.exceptions import NotFound, PermissionDenied, ParseError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from authentication.models import DeviceSession
from django.utils import timezone
from .services import available_rooms, describe, room_for
from . import protocol, serializers


class BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        from io import BytesIO
        value = stream.read(131073)
        if len(value) > 131072:
            raise ParseError("Chat requests are limited to 128 KiB.")
        return super().parse(BytesIO(value), media_type, parser_context)


class ChatReadThrottle(UserRateThrottle):
    rate = "240/min"
    scope = "couple_chat_reads"


class ChatWriteThrottle(UserRateThrottle):
    rate = "120/min"
    scope = "couple_chat_writes"


class ChatKeyThrottle(UserRateThrottle):
    rate = "12/min"
    scope = "couple_chat_keys"


class ChatView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ChatReadThrottle]
    parser_classes = [BoundedJSONParser]

    def get_throttles(self):
        if self.request.method in ("POST", "PUT", "DELETE"):
            return [ChatKeyThrottle() if "/devices/" in self.request.path else ChatWriteThrottle()]
        return super().get_throttles()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.COUPLE_CHAT_PREVIEW_ENABLED:
            raise NotFound()
        sid = request.auth.get("sid") if request.auth else None
        if not sid or not DeviceSession.objects.filter(pk=sid, user=request.user,
                revoked_at__isnull=True, expires_at__gt=timezone.now()).exists():
            raise PermissionDenied("A current device session is required.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, protocol.Conflict):
            # Additive to the application's existing error envelope, including core-1.
            response.data["chat_code"] = str(exc.detail["code"])
        return response

    def validated(self, request, serializer):
        value = serializer(data=request.data)
        value.is_valid(raise_exception=True)
        return value.validated_data


class StateView(ChatView):
    def get(self, request):
        room = available_rooms(request.user).first()
        return Response({"data": {"contract": "couple-chat-1", "room": describe(room) if room else None,
            "devices": protocol.device_state(request.user, request.auth["sid"], room)}})


class RoomView(ChatView):
    def get(self, request, room_id):
        return Response({"data": describe(room_for(request.user, room_id))})


class DeviceView(ChatView):
    def post(self, request):
        return Response({"data": protocol.register(request.user, request.auth["sid"],
            self.validated(request, serializers.Registration))})


class ParticipantDeviceView(ChatView):
    def get(self, request, room_id, device_id):
        room = room_for(request.user, room_id)
        device = protocol.Device.objects.filter(pk=device_id, user_id__in=[room.first_id, room.second_id]).first()
        if device is None:
            raise NotFound()
        return Response({"data": protocol.device_info(device)})


class KeysView(ChatView):
    def post(self, request):
        return Response({"data": protocol.replenish(request.user, request.auth["sid"],
            self.validated(request, serializers.Publication))})


class ClaimView(ChatView):
    def post(self, request, room_id):
        return Response({"data": protocol.claim_bundle(request.user, request.auth["sid"], room_id,
            self.validated(request, serializers.Claim))})


class OperationsView(ChatView):
    def post(self, request, room_id):
        return Response({"data": protocol.accept_operation(request.user, request.auth["sid"], room_id,
            self.validated(request, serializers.Envelope))})


class SyncView(ChatView):
    def get(self, request, room_id):
        query = serializers.SyncQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response({"data": protocol.synchronize(request.user, request.auth["sid"], room_id, query.validated_data)})
