import json
from chat.utils.chatutils import *
from channels.generic.websocket import AsyncWebsocketConsumer
from .message_sender import handle_send_message
from .message_retriever import handle_fetch_messages
from .message_editor import handle_edit_message, handle_mark_messages_read, handle_react_to_message
from .message_deleter import handle_delete_message

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("INSIDE CONNECT METHOD")
            
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
        data = json.loads(text_data)
        action = data.get("action")
        
        print("INSIDE ON RECEIVE METHOD", data)
        
        if action == "send_message":
            message = await handle_send_message(data, self.chat_room)
            if message:
                serialized_message = await serialize_message(message)
                print(f"Serialized message: {serialized_message}")
                
                # BROADCAST to all clients in the room group
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",  # This calls the chat_message method below
                        "action": "send_message",
                        "message": "message_sent successfully",
                        "data": serialized_message,
                    }
                )
            else:
                # Only send error to the sender
                await self.send(text_data=json.dumps({
                    "message": "message_sent failed",
                }))
                        
        elif action == "fetch_messages":
            print("Fetching messages...")
            result = await handle_fetch_messages(
                user=self.scope.get("user").id,  # Use actual user from scope
                chat_room=self.chat_room_id,
                data=data
            )
        
            if result["messages"]:
                messages_list = []
                for msg in result["messages"]:
                    print(f"message is available = {msg.text}")
                    serialized_message = await serialize_message(msg)
                    messages_list.append(serialized_message)
                    
                await self.send(text_data=json.dumps({
                    "action": "fetch_messages",
                    "message": "Messages fetched successfully!",
                    "data": messages_list,
                    "pagination": result["pagination"]
                }))
            else:
                await self.send(text_data=json.dumps({
                    "action": "fetch_messages",
                    "message": "No messages found",
                    "data": [],
                    "pagination": result["pagination"]
                }))
            
        elif action == "edit_message":
            edited_message = await handle_edit_message(data=data)
            if edited_message:
                serialized_message = await serialize_message(edited_message)
                
                # BROADCAST edited message to all clients
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
                
                # BROADCAST read status to all clients    
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
            
            # BROADCAST deletion to all clients
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
                
                # BROADCAST reaction to all clients
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

    # This method receives messages from channel layer and sends to WebSocket
    async def chat_message(self, event):
        """Handler for messages sent via channel_layer.group_send"""
        await self.send(text_data=json.dumps({
            "action": event["action"],
            "message": event["message"],
            "data": event.get("data")
        }))