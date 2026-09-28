from __future__ import annotations

import asyncio
import json
import logging
from time import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from rest_framework.exceptions import ValidationError

from chat.consumers.handlers import ChatSocketHandlerMixin
from chat.middleware import user_is_active
from chat.selectors.chat_room_selectors import get_chat_room_for_user
from chat.websocket.protocol import CommandBudget, decode_command
from chat.websocket.responses import socket_error, socket_event
from srisu.api.errors import field_codes

logger = logging.getLogger('srisu.socket')


class ChatConsumer(ChatSocketHandlerMixin, AsyncWebsocketConsumer):
    async def connect(self):
        self.room_subscriptions = set()
        self.watchdog = None
        self.budget = CommandBudget()
        user = self.scope.get('user')
        if not user or user.is_anonymous or not await self.session_valid():
            await self.close(code=4401)
            return
        self.user = user
        self.user_group_name = self.get_user_group_name(user.id)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()
        self.watchdog = asyncio.create_task(self.watch_access())

    async def session_valid(self):
        user = self.scope.get('user')
        expiry = self.scope.get('access_expires_at')
        return bool(user and user.is_authenticated and expiry and expiry > time()
                    and await user_is_active(user.pk, self.scope.get("device_session_id")))

    async def watch_access(self):
        try:
            while True:
                await asyncio.sleep(min(15, max(0.01, self.scope['access_expires_at'] - time())))
                if not await self.session_valid():
                    await self.close(code=4401)
                    return
                for room_id in tuple(self.room_subscriptions):
                    if not await self.room_allowed(room_id):
                        await self.remove_subscription(room_id)
                        await self.send_json(socket_event(action='access_revoked', data={'chat_room_id': room_id}))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("socket_access_check_unavailable")
            await self.close(code=1011)

    async def disconnect(self, close_code):
        if self.watchdog:
            self.watchdog.cancel()
            try:
                await self.watchdog
            except asyncio.CancelledError:
                pass
        for room_id in tuple(self.room_subscriptions):
            await self.remove_subscription(room_id)
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not await self.session_valid():
            await self.close(code=4401)
            return
        try:
            command = decode_command(text_data)
        except ValidationError as exc:
            await self.send_json(socket_error(message='Invalid command.', status=exc.status_code, fields=field_codes(exc.get_codes()), request_id=getattr(exc, 'request_id', None), action=getattr(exc, 'action', None)))
            return
        if not self.budget.allow():
            await self.send_json(socket_error(message='Please wait before trying again.', status=429, request_id=command.get('request_id')))
            return
        if command['action'] == 'unsubscribe_room':
            await self.remove_subscription(command['payload']['chat_room_id'])
            from chat.websocket.responses import socket_success
            await self.send_json(socket_success(action='unsubscribe_room', data={}, request_id=command.get('request_id')))
            return
        response = await self.handle_action(action=command['action'], payload=command['payload'], request_id=command.get('request_id'))
        if not await self.session_valid():
            await self.close(code=4401)
            return
        data = response.get('data') or {}
        room_id = data.get('chat_room_id') or (data.get('message') or {}).get('chat_room_id') or (data.get('chat_room') or {}).get('id')
        if room_id and not await self.room_allowed(room_id):
            response = socket_error(action=command['action'], status=404, message='Resource unavailable.', request_id=command.get('request_id'))
        elif 'chat_rooms' in data:
            data['chat_rooms'] = [room for room in data['chat_rooms'] if await self.room_allowed(room['id'])]
        await self.send_json(response)

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))

    @staticmethod
    def get_room_group_name(chat_room_id):
        return f'chat_room_{chat_room_id}'

    @staticmethod
    def get_user_group_name(user_id):
        return f'chat_user_{user_id}'

    async def room_allowed(self, room_id):
        return await database_sync_to_async(get_chat_room_for_user)(room_id, self.user) is not None

    async def ensure_room_subscription(self, chat_room_id):
        if not await self.room_allowed(chat_room_id):
            return False
        if chat_room_id not in self.room_subscriptions and len(self.room_subscriptions) >= settings.WS_MAX_SUBSCRIPTIONS:
            # Explicit bounded subscriptions; historical rooms remain HTTP-readable.
            return False
        await self.channel_layer.group_add(self.get_room_group_name(chat_room_id), self.channel_name)
        self.room_subscriptions.add(chat_room_id)
        return True

    async def remove_subscription(self, room_id):
        await self.channel_layer.group_discard(self.get_room_group_name(room_id), self.channel_name)
        self.room_subscriptions.discard(room_id)

    async def broadcast_to_room(self, *, chat_room_id, event):
        event = {**event, 'room_id': chat_room_id}
        try:
            await self.channel_layer.group_send(self.get_room_group_name(chat_room_id), {'type': 'chat.broadcast', 'room_id': chat_room_id, 'payload': event})
        except Exception:
            # The database write has committed. Do not turn publication failure
            # into a false send failure; HTTP history is the recovery boundary.
            logger.warning('socket_publication_unavailable')

    async def broadcast_to_user(self, *, user_id, event, room_id):
        try:
            await self.channel_layer.group_send(self.get_user_group_name(user_id), {'type': 'chat.broadcast', 'room_id': room_id, 'payload': {**event, 'room_id': room_id}})
        except Exception:
            logger.warning('socket_publication_unavailable')

    async def broadcast_chat_room_update_to_participants(self, chat_room, room_payload):
        for participant in (chat_room.user_one, chat_room.user_two):
            if participant:
                await self.broadcast_to_user(user_id=participant.id, room_id=str(chat_room.pk), event=socket_event(action='chat_room_updated', data=room_payload))

    async def chat_broadcast(self, event):
        if not await self.session_valid():
            await self.close(code=4401)
            return
        room_id = event.get('room_id')
        if not room_id or not await self.room_allowed(room_id):
            if room_id:
                await self.remove_subscription(room_id)
                await self.send_json(socket_event(action='access_revoked', data={'chat_room_id': room_id}))
            return
        await self.send_json(event['payload'])

    async def serialize_message(self, message):
        from chat.presenters.message_presenter import serialize_message_for_socket
        return await serialize_message_for_socket(message, self.scope)

    async def serialize_chat_room_preview(self, chat_room):
        from chat.presenters.chat_room_presenter import serialize_chat_room_preview_for_socket
        return await serialize_chat_room_preview_for_socket(chat_room, self.user, self.scope)

    async def serialize_chat_room_list_item(self, chat_room):
        from chat.presenters.chat_room_presenter import serialize_chat_room_list_item_for_socket
        return await serialize_chat_room_list_item_for_socket(chat_room, self.user, self.scope)
