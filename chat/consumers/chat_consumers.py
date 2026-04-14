from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from chat.consumers.handlers import ChatSocketHandlerMixin
from chat.websocket.responses import socket_error

logger = logging.getLogger(__name__)


class ChatConsumer(ChatSocketHandlerMixin, AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close()
            return

        self.user = user
        self.user_group_name = self.get_user_group_name(user.id)

        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "user_group_name"):
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            incoming = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json(
                socket_error(message="Invalid JSON payload")
            )
            return

        action = incoming.get("action")
        request_id = incoming.get("request_id")
        payload = incoming.get("payload", {})

        response = await self.handle_action(
            action=action,
            payload=payload,
            request_id=request_id,
        )
        await self.send_json(response)

    async def send_json(self, content: dict):
        await self.send(text_data=json.dumps(content))

    @staticmethod
    def get_room_group_name(chat_room_id: str) -> str:
        return f"chat_room_{chat_room_id}"

    @staticmethod
    def get_user_group_name(user_id: int) -> str:
        return f"chat_user_{user_id}"
    
    async def ensure_room_subscription(self, chat_room_id: str):
        room_group_name = self.get_room_group_name(chat_room_id)
        print(f"Adding {self.channel_name} to room group {room_group_name}")
        await self.channel_layer.group_add(room_group_name, self.channel_name)

    async def broadcast_to_room(self, *, chat_room_id: str, event: dict):
        await self.channel_layer.group_send(
            self.get_room_group_name(chat_room_id),
            {
                "type": "chat.broadcast",
                "payload": event,
            },
        )

    async def broadcast_to_user(self, *, user_id: int, event: dict):
        await self.channel_layer.group_send(
            self.get_user_group_name(user_id),
            {
                "type": "chat.broadcast",
                "payload": event,
            },
        )

    async def broadcast_chat_room_update_to_participants(self, chat_room, room_payload: dict):
        participants = [chat_room.user_one, chat_room.user_two]
        for participant in participants:
            if not participant:
                continue
            await self.broadcast_to_user(
                user_id=participant.id,
                event={
                    "type": "event",
                    "action": "chat_room_updated",
                    "message": "Chat room updated",
                    "data": room_payload,
                },
            )
    
    async def broadcast_message_sent_to_all_clients(self, chat_room_id: str, message_payload: dict):
        await self.channel_layer.group_send(
            self.get_room_group_name(chat_room_id),
            {
                "type": "chat.broadcast",
                "payload": {
                    "type": "event",
                    "action": "message_created",
                    "message": "New message created",
                    "data": message_payload,
                },
            }
        )

    async def chat_broadcast(self, event):
        await self.send_json(event["payload"])

    async def serialize_message(self, message):
        from chat.presenters.message_presenter import serialize_message_for_socket
        return await serialize_message_for_socket(message, self.scope)

    async def serialize_chat_room_preview(self, chat_room):
        from chat.presenters.chat_room_presenter import serialize_chat_room_preview_for_socket
        return await serialize_chat_room_preview_for_socket(chat_room, self.user, self.scope)

    async def serialize_chat_room_list_item(self, chat_room):
        from chat.presenters.chat_room_presenter import serialize_chat_room_list_item_for_socket
        return await serialize_chat_room_list_item_for_socket(chat_room, self.user, self.scope)