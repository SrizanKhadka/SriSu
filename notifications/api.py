from datetime import timedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.urls import path
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from authentication.models import DeviceSession
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ParseError
from .models import Delivery, Installation, Preferences
from .services import BindingConflict, destination, register


class BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        from io import BytesIO
        value = stream.read(16385)
        if len(value) > 16384:
            raise ParseError("Notification requests are limited to 16 KiB.")
        return super().parse(BytesIO(value), media_type, parser_context)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Unknown fields or invalid object."]})
        return super().to_internal_value(data)


class Registration(StrictSerializer):
    installation_id = serializers.UUIDField()
    proof = serializers.RegexField(r"^[A-Za-z0-9_-]{43,128}$", trim_whitespace=False)
    version = serializers.IntegerField(min_value=1, max_value=9007199254740991)
    platform = serializers.ChoiceField(choices=["android", "ios"])
    target_kind = serializers.ChoiceField(choices=["fid", "token"])
    target = serializers.CharField(min_length=16, max_length=4096, trim_whitespace=False)
    permitted = serializers.BooleanField()
    capabilities = serializers.ListField(child=serializers.ChoiceField(choices=["matrix-chat-push-1"]),
        max_length=1, required=False, default=list)


class PreferenceInput(StrictSerializer):
    expected_revision = serializers.IntegerField(min_value=1)
    push = serializers.BooleanField()
    chat = serializers.BooleanField()
    activity = serializers.BooleanField()
    mute = serializers.ChoiceField(choices=["unchanged", "off", "1h", "8h", "forever"])


class Rate(UserRateThrottle):
    rate = "60/min"
    scope = "notification_v1"


class NotificationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [Rate]
    parser_classes = [BoundedJSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        sid = request.auth.get("sid") if request.auth else None
        if not sid or not DeviceSession.objects.filter(pk=sid, user=request.user,
                revoked_at__isnull=True, expires_at__gt=timezone.now()).exists():
            raise PermissionDenied("A current device session is required.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class InstallationView(NotificationView):
    def put(self, request):
        serializer = Registration(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            install = register(request.user, request.auth["sid"], serializer.validated_data)
        except IntegrityError:
            raise BindingConflict() from None
        return Response({"data": {"installation_id": str(install.pk), "version": install.version}})

    def delete(self, request):
        # Revoke this login's bindings, never a new login that took over the same installation.
        with transaction.atomic():
            session = DeviceSession.objects.select_for_update().get(pk=request.auth["sid"])
            Installation.objects.filter(user=request.user, session=session).update(active=False)
        return Response(status=204)


def preferences_data(pref):
    return {"contract": "notifications-1", "push": pref.push, "chat": pref.chat, "activity": pref.activity,
        "mute_until": pref.mute_until, "mute_forever": pref.mute_forever, "revision": pref.revision,
        "capabilities": ["activity-notifications-1", "spark-notifications-1"] +
            (["matrix-chat-push-1"] if settings.MATRIX_CHAT_PUSH_ENABLED else [])}


class PreferencesView(NotificationView):
    def get(self, request):
        return Response({"data": preferences_data(Preferences.objects.get_or_create(user=request.user)[0])})

    @transaction.atomic
    def put(self, request):
        serializer = PreferenceInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # User lock also serializes first preference creation.
        from authentication.models import UserModel
        UserModel.objects.select_for_update().get(pk=request.user.pk)
        pref, _ = Preferences.objects.get_or_create(user=request.user)
        if pref.revision != data["expected_revision"]:
            raise BindingConflict()
        for key in ("push", "chat", "activity"):
            setattr(pref, key, data[key])
        if data["mute"] != "unchanged":
            pref.mute_forever = data["mute"] == "forever"
            pref.mute_until = timezone.now() + timedelta(hours={"1h": 1, "8h": 8}[data["mute"]]) if data["mute"] in ("1h", "8h") else None
        pref.revision += 1
        pref.save()
        return Response({"data": preferences_data(pref)})


class ResolveView(NotificationView):
    def get(self, request, delivery_id):
        delivery = Delivery.objects.select_related("event").filter(pk=delivery_id,
            event__recipient=request.user, installation__user=request.user,
            installation__session_id=request.auth["sid"], installation__active=True).first()
        if delivery is None or delivery.binding_version != delivery.installation.version:
            raise NotFound()
        return Response({"data": destination(delivery.event, request.user, request.auth["sid"])})


urlpatterns = [path("v1/installations/", InstallationView.as_view()),
    path("v1/preferences/", PreferencesView.as_view()),
    path("v1/deliveries/<uuid:delivery_id>/", ResolveView.as_view())]
