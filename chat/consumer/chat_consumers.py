import json
import traceback
from chat.utils.chatutils import *
from channels.generic.websocket import AsyncWebsocketConsumer
from .message_sender import handle_send_message
from .message_retriever import get_paginated_messages
from .message_editor import handle_edit_message, handle_mark_messages_read, handle_react_to_message
from .message_deleter import handle_delete_message

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("INSIDE CONNECT METHOD")
        
        # Initialize attributes to avoid AttributeError in disconnect
        self.room_group_name = None
        self.chat_room = None
        
        # Check if user is authenticated
        user = self.scope.get("user")
        # if not user or user.is_anonymous:
        #     print("User is anonymous, closing connection")
        #     await self.close()
        #     return
            
        self.chat_room_id = self.scope["url_route"]["kwargs"]["room_id"]
        self.room_group_name = f"chat_{self.chat_room_id}"
        self.chat_room = await get_chat_room(self.chat_room_id)
        
        print(f"Chat room ID: {self.chat_room_id}")
        print(f"Chat room: {self.chat_room}")
        
        if not self.chat_room:
            print("Chat room not found, closing connection")
            await self.close()
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
    
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            action = data.get("action")
            
            print("INSIDE ON RECEIVE METHOD", data)
            
            if action == "send_message":
                message = await handle_send_message(data, self.chat_room)
                if message:
                    serialized_message = await serialize_message(message)                    
                    # BROADCAST to all clients in the room group
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "send_message",
                            "message": "message_sent successfully",
                            "data": serialized_message,
                        }
                    )
                else:
                    await self.send(text_data=json.dumps({
                        "message": "message_sent failed",
                    }))
                            
            elif action == "fetch_messages":
                
                result = await get_paginated_messages(
                    chat_room=self.chat_room_id,
                    user=self.scope.get("user"),
                    page=data.get("page", 1),
                    page_size=data.get("page_size", 20)
                )

                # Only send to requesting client (not broadcast)
                await self.send(
                    text_data=json.dumps({
                        "action": "fetch_messages",
                        "message": "Messages fetched successfully" if result["results"] else "No messages found",
                        "data": result,
                        "success": True
                    })
                )
                
            elif action == "edit_message":
                edited_message = await handle_edit_message(data=data)
                if edited_message:
                    serialized_message = await serialize_message(edited_message)
                    
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "edit_message",
                            "message": "message edited successfully",
                            "data": serialized_message,
                        }
                    )
                    
            elif action == "message_read":
                read_messages = await handle_mark_messages_read(data=data)
                read_messages_list = []
                
                if read_messages:
                    for msg in read_messages:
                        serialized_message = await serialize_message(msg)
                        read_messages_list.append(serialized_message)
                    
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "message_read",
                            "message": "message read successfully!",
                            "data": read_messages_list
                        }
                    )
                else:
                    await self.send(text_data=json.dumps({
                        "action": "message_read",
                        "message": "No unread messages found!",
                    }))
                    
            elif action == "delete_message":
                deleted_msg_id = await handle_delete_message(data, on_message_deleted=lambda msg_id:  
                    print('MESSAGES DELETED SUCCESSFULLY.')
                )
                
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "action": "delete_message",
                        "message": "message deleted successfully",
                        "data": {"message_id": data.get("message_id")}
                    }
                )
                
            elif action == "react_to_message":
                reacted_message = await handle_react_to_message(data=data)
                if reacted_message:
                    serialized_message = await serialize_message(reacted_message)
                    
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "react_to_message",
                            "message": "message reacted successfully",
                            "data": serialized_message,
                        }
                    )
                else:
                    await self.send(text_data=json.dumps({
                        "action": "react_to_message",
                        "message": "message reaction failed",
                    }))
                    
        except Exception as e:
            print(f"Error in receive: {e}")
            traceback.print_exc()

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "action": event["action"],
            "message": event["message"],
            "data": event.get("data")
        }))