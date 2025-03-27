import json
import uuid
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.db.models import Q
from chat.models import ChatRoom, MessageModel
from authentication.models import UserModel
from channels.db import database_sync_to_async
from utils.choices import DeleteOption

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

    async def receive(self, message_data):
        data = json.loads(message_data)
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
        couple = data.get("couple")
        singles = data.get("single")
        sender_id = data.get("sender_id")
        receiver_id = data.get("receiver_id")
        text = data.get("text", "")
        message_type = data.get("message_type", "text")
        medias = data.get("medias")
        reply_to_id = data.get("reply_to")
        timestamp = data.get("timestamp")
        
        sender = await self.get_user(sender_id)
        if not sender or not self.chat_room:
            return
        
        reply_to = await self.get_message(reply_to_id) if reply_to_id else None
        
        new_message = await self.create_message(
            chat_room=self.chat_room,
            couple = couple,
            singles = singles,
            sender=sender,
            receiver=receiver_id,
            message_type=message_type,
            text=text,
            is_delivered=True,
            timestamp = timestamp,
            medias=medias,
            reply_to=reply_to,
        )
        
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "chat.message", "message": self.serialize_message(new_message)}
        )
    
    async def handle_edit_message(self, data):
        message_id = data.get("message_id")
        new_text = data.get("new_text")
        is_read = data.get("is_read", False)
        
        message = await self.get_message(message_id)
        if message:
            message.text = new_text
            message.is_delivered = True
            message.is_read = is_read
            message.is_edited = True
            await self.save_message(message)
            
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message", "message": self.serialize_message(message)}
            )
    
    async def handle_mark_messages_read(self, data):
        receiver_id = data.get("receiver_id")
    
        if not receiver_id:
            return

        # Fetch all unread messages sent to this receiver
        unread_messages = await database_sync_to_async(
        lambda: list(MessageModel.objects.filter(receiver=receiver_id, is_read=False))
        )()

        if unread_messages:
        # Bulk update messages as read
            for message in unread_messages:
                message.is_read = True

            await database_sync_to_async(MessageModel.objects.bulk_update)(
            unread_messages, ["is_read"]
            )

        # Notify all participants that messages are now read
            await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.messages_read",
                "message_ids": [msg.id for msg in unread_messages],
            },
         )


    async def handle_delete_message(self, data):
        message_id = data.get("message_id")
        delete_option = data.get('delete_option')
        message = await self.get_message(messasge_id=message_id)
        
        if message.delete_option == DeleteOption.DELETE_FOR_EVERYONE or message.delete_option == DeleteOption.DELETE_FOR_ME:
           message.is_deleted = True
           message.delete_option = DeleteOption.DELETED
           await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message.deleted", "message_id": message_id}
            )
        elif delete_option == DeleteOption.DELETE_FOR_ME:
            message.delete_option =  DeleteOption.DELETE_FOR_ME
            message.deleted_message = "This message was deleted."
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message.deleted_for_me", "message_id": message_id}
            )
        else:
            message.delete_option =  DeleteOption.DELETE_FOR_EVERYONE
            message.deleted_message = "This message was deleted."
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message.deleted_for_everyone", "message_id": message_id}
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
            "medias": message.medias if message.medias else None,
            "reply_to": str(message.reply_to.id) if message.reply_to else None,
            "reaction": message.reaction,
            "is_read": message.is_read,
            "is_delivered": message.is_delivered,
            "timestamp": message.timestamp.isoformat(),
        }
