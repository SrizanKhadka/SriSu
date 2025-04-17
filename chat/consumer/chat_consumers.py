import json
from chat.utils.chatutils import *
from channels.generic.websocket import AsyncWebsocketConsumer
from .message_sender import handle_send_message
from .message_retriever import handle_fetch_messages
from .message_editor import handle_edit_message, handle_mark_messages_read, handle_react_to_message
from .message_deleter import handle_delete_message

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        
        # self.user = self.scope["user"]
        # if self.user.is_anonymous:
        #     await self.close()
        # else:
        #     await self.accept()
            
        self.chat_room_id = self.scope["url_route"]["kwargs"]["room_id"]
        self.room_group_name = f"chat_{self.chat_room_id}"
        self.chat_room = await get_chat_room(self.chat_room_id)
        
        print(f"Chat room ID: {self.chat_room_id}")
        print(f"Chat room: {self.chat_room}")
        

        if not self.chat_room:
            await self.close()
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
    

    async def receive(self, text_data):
        print(f"Received message: {text_data}")
        data = json.loads(text_data)
        action = "send_message"
        
        if action == "send_message":

            message = await handle_send_message(data,self.chat_room)
            if message:
                print(f"MESSAGE TEXT : {message.text}")
                await self.send(
                    text_data=json.dumps(
                        {
                            "message": "message_sent successfully",
                        }
                    )
                )
        
            else:
                await self.send(text_data=json.dumps(
                    {
                        "message": "message_sent failed",
                    }
                ))
            
           
            # await self.close()
            
        elif action == "fetch_messages":
            messages = await handle_fetch_messages(
                user=5,                 # user=self.user,
                chat_room=self.chat_room,
                data=data
            )
            
            if messages:
                for mgs in messages:
                    print(f"message = {mgs.text}")
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "fetch_messages",
                        }
                    )
                )
            
        elif action == "edit_message":
            await handle_edit_message(data=data, on_message_edited=lambda msg: 
             self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat.message", "message": self.serialize_message(msg)},
            ))
        elif action == "message_read":
            await handle_mark_messages_read(data=data,on_messages_read=lambda unread_messages: self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.messages_read",
                "message_ids": [msg.id for msg in unread_messages],
            },
            ))
        elif action == "delete_message":
            await handle_delete_message(data, on_message_deleted=lambda msg_id:  self.channel_layer.group_send(
            self.room_group_name,
            {"type": "chat.message_delete", "message_ids": msg_id},
            ))
            
        elif action == "react_to_message":
            await handle_react_to_message(data=data, on_message_reacted=lambda msg: self.channel_layer.group_send(
            self.room_group_name,
            {"type": "chat.message", "message": self.serialize_message(msg)},
            ))



    async def handle_fetch_messages(self, user, data):
        page = int(data.get("page", 1))
        page_size = int(data.get("page_size", 20))
        messages = await self.get_paginated_messages(
            self.chat_room, user, page, page_size
        )

        await self.send(
            text_data=json.dumps(
                {
                    "action": "fetch_messages",
                    "messages": [self.serialize_message(msg) for msg in messages],
                }
            )
        )