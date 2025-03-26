import json
import uuid
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.db.models import Q
from chat.models import ChatRoom, MessageModel
from authentication.models import UserModel

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.chat_room_id = self.scope["url_route"]["kwargs"]["chat_room_id"]
        self.room_group_name = f"chat_{self.chat_room_id}"
        self.chat_room = await self.get_chat_room(self.chat_room_id)
        
        if not self.chat_room:
            await self.close()
            return
        
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get("action")

        if action == "send_message":
            await self.handle_send_message(data)
        elif action == "fetch_messages":
            await self.handle_fetch_messages(data)
        elif action == "edit_message":
            await self.handle_edit_message(data)
        elif action == "delete_message":
            await self.handle_delete_message(data)
        elif action == "react_to_message":
            await self.handle_react_to_message(data)

    async def handle_send_message(self, data):
        sender_id = data.get("sender_id")
        text = data.get("text", "")
        message_type = data.get("message_type", "text")
        media_url = data.get("media_url")
        reply_to_id = data.get("reply_to")
        
        sender = await self.get_user(sender_id)
        if not sender or not self.chat_room:
            return
        
        reply_to = await self.get_message(reply_to_id) if reply_to_id else None
        
        new_message = await self.create_message(
            chat_room=self.chat_room,
            sender=sender,
            text=text,
            message_type=message_type,
            media_url=media_url,
            reply_to=reply_to,
        )
        
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "chat.message", "message": self.serialize_message(new_message)}
        )
    
    async def handle_edit_message(self, data):
        message_id = data.get("message_id")
        new_text = data.get("new_text")
        
        message = await self.get_message(message_id)
        if message:
            message.text = new_text
            await self.save_message(message)
            
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message", "message": self.serialize_message(message)}
            )

    async def handle_delete_message(self, data):
        message_id = data.get("message_id")
        
        message = await self.get_message(message_id)
        if message:
            await self.delete_message(message)
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message.deleted", "message_id": message_id}
            )
    
    async def handle_react_to_message(self, data):
        message_id = data.get("message_id")
        reaction = data.get("reaction")
        
        message = await self.get_message(message_id)
        if message:
            message.reaction = reaction
            await self.save_message(message)
            
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message", "message": self.serialize_message(message)}
            )

    async def handle_fetch_messages(self, data):
        page = int(data.get("page", 1))
        page_size = int(data.get("page_size", 20))
        messages = await self.get_paginated_messages(self.chat_room, page, page_size)
        
        await self.send(text_data=json.dumps({
            "action": "fetch_messages",
            "messages": [self.serialize_message(msg) for msg in messages]
        }))
    
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))
    
    async def chat_message_deleted(self, event):
        await self.send(text_data=json.dumps({"action": "delete_message", "message_id": event["message_id"]}))

    @sync_to_async
    def get_chat_room(self, chat_room_id):
        return ChatRoom.objects.filter(id=chat_room_id).first()

    @sync_to_async
    def get_user(self, user_id):
        return UserModel.objects.filter(id=user_id).first()

    @sync_to_async
    def get_message(self, message_id):
        return MessageModel.objects.filter(id=message_id).first()

    @sync_to_async
    def create_message(self, **kwargs):
        return MessageModel.objects.create(**kwargs)

    @sync_to_async
    def save_message(self, message):
        message.save()

    @sync_to_async
    def delete_message(self, message):
        message.delete()

    @sync_to_async
    def get_paginated_messages(self, chat_room, page, page_size):
        offset = (page - 1) * page_size
        return list(MessageModel.objects.filter(chat_room=chat_room).order_by("-timestamp")[offset:offset + page_size])

    def serialize_message(self, message):
        return {
            "id": str(message.id),
            "chat_room_id": str(message.chat_room.id),
            "sender_id": str(message.sender.id),
            "text": message.text,
            "message_type": message.message_type,
            "media_url": message.media_url if message.media_url else None,
            "reply_to": str(message.reply_to.id) if message.reply_to else None,
            "reaction": message.reaction,
            "is_read": message.is_read,
            "is_delivered": message.is_delivered,
            "timestamp": message.timestamp.isoformat(),
        }
