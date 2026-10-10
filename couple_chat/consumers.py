"""Authenticated invalidations and disposable, content-free foreground activity."""
import asyncio
import contextlib
import json
import time
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from authentication.models import DeviceSession
from authentication.sessions import SessionJWTAuthentication
from authentication.models import UserModel
from rest_framework.exceptions import NotFound
from .services import room_for
from .protocol import live_devices


@database_sync_to_async
def activity_access(user_id, sid, room_id, device_id, receiver=None):
    user = UserModel.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None
    try:
        room = room_for(user, room_id)
    except NotFound:
        return None
    device = live_devices().filter(pk=device_id, user_id=user_id, session_id=sid).first()
    if device is None:
        return None
    partner = room.second_id if room.first_id == user_id else room.first_id
    if receiver is not None and receiver != partner:
        return None
    return partner


@database_sync_to_async
def authenticate(headers):
    # New protocol deliberately accepts only native Bearer headers, never URL tokens.
    values = [v for k, v in headers if k.lower() == b"authorization"]
    if len(values) != 1 or not values[0].startswith(b"Bearer "):
        return None
    try:
        auth = SessionJWTAuthentication()
        token = auth.get_validated_token(values[0][7:].decode("ascii"))
        user = auth.get_user(token)
        sid = token.get("sid")
        if not sid or not user.is_phone_verified or not user.is_profile_complete:
            return None
        return user.pk, sid, int(token["exp"])
    except (AuthenticationFailed, InvalidToken, TokenError, ValueError, KeyError, UnicodeError):
        return None


@database_sync_to_async
def session_current(user_id, sid):
    return DeviceSession.objects.filter(pk=sid, user_id=user_id, revoked_at__isnull=True,
        expires_at__gt=timezone.now(), user__is_active=True,
        user__is_phone_verified=True, user__is_profile_complete=True).exists()


class UserUpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.expiry_task = None
        self.group = None
        self.activity = None
        self.activity_budget = 4.0
        self.activity_clock = time.monotonic()
        if not settings.COUPLE_CHAT_PREVIEW_ENABLED:
            await self.close(code=4404)
            return
        headers = self.scope.get("headers", [])
        origin = dict(headers).get(b"origin")
        if origin and origin.decode(errors="replace") not in settings.WEBSOCKET_ALLOWED_ORIGINS:
            await self.close(code=4403)
            return
        identity = await authenticate(headers)
        self.scope["headers"] = [(k, v) for k, v in headers if k.lower() != b"authorization"]
        self.scope["query_string"] = b""
        if identity is None:
            await self.close(code=4401)
            return
        self.user_id, self.sid, self.expires_at = identity
        self.group = f"couple_chat.user.{self.user_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self.expiry_task = asyncio.create_task(self.watch_session())
        # Subscribing precedes snapshot invalidation, closing the connection-time gap.
        await self.state_changed({})

    async def valid(self):
        return time.time() < self.expires_at and await session_current(self.user_id, self.sid)

    async def watch_session(self):
        while True:
            await asyncio.sleep(min(5, max(0.1, self.expires_at - time.time())))
            if not await self.valid():
                await self.close(code=4401)
                return

    async def state_changed(self, event):
        if not await self.valid():
            await self.close(code=4401)
            return
        await self.send_json({"version": 1, "type": "state.changed"})

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if bytes_data is not None or text_data is None or len(text_data.encode("utf-8")) > 512:
            await self.close(code=4400)
            return
        try:
            value = json.loads(text_data)
            if (not isinstance(value, dict) or set(value) != {"version", "type", "room_id", "device_id", "online", "typing"}
                or type(value["version"]) is not int or value["version"] != 1 or value["type"] != "activity"
                or type(value["online"]) is not bool or type(value["typing"]) is not bool
                or (value["typing"] and not value["online"])):
                raise ValueError()
            room_id, device_id = str(UUID(value["room_id"])), str(UUID(value["device_id"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            await self.close(code=4400)
            return
        now = time.monotonic()
        self.activity_budget = min(4, self.activity_budget + now - self.activity_clock)
        self.activity_clock = now
        if self.activity_budget < 1:
            await self.close(code=4429)
            return
        self.activity_budget -= 1
        if not await self.valid():
            await self.close(code=4401)
            return
        partner = await activity_access(self.user_id, self.sid, room_id, device_id)
        if partner is None:
            await self.close(code=4403)
            return
        if self.activity and self.activity[0] != room_id:
            await self.broadcast_activity(False, False)
        self.activity = (room_id, device_id, partner)
        await self.broadcast_activity(value["online"], value["typing"])

    async def broadcast_activity(self, online, typing):
        if self.activity:
            room, device, partner = self.activity
            await self.channel_layer.group_send(f"couple_chat.user.{partner}", {
                "type": "partner.activity", "room": room, "device": device,
                "actor": self.user_id, "sid": self.sid, "online": online,
                "typing": typing, "sent_at": time.time(),
            })

    async def partner_activity(self, event):
        if not await self.valid():
            await self.close(code=4401)
            return
        # Recheck both memberships, blocking and the sender's current device at
        # delivery time. Delayed channel messages never renew an expired status.
        age = time.time() - event["sent_at"]
        if age < 0 or age >= 25 or await activity_access(event["actor"], event["sid"], event["room"], event["device"], self.user_id) is None:
            return
        await self.send_json({"version": 1, "type": "partner.activity", "room_id": event["room"],
            "online": event["online"], "typing": event["typing"],
            "expires_in_ms": max(0, int((25-age)*1000)) if event["online"] else 0,
            "typing_expires_in_ms": max(0, int((5-age)*1000)) if event["typing"] else 0})

    async def disconnect(self, code):
        await self.broadcast_activity(False, False)
        if self.expiry_task:
            self.expiry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.expiry_task
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)
