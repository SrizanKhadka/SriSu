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
        action = "delete_message"
        
        if action == "send_message":

            message = await handle_send_message(data,self.chat_room)
            if message:
                serialized_message = await serialize_message(message)
                print(f"Serialized message: {serialized_message}")
                await self.send(
                    text_data=json.dumps(
                        {
                            "message": "message_sent successfully",
                            "data": serialized_message,
                        }
                    )
                )
        
            else:
                await self.send(text_data=json.dumps(
                    {
                        "message": "message_sent failed",
                    }
                ))
                        
        elif action == "fetch_messages":
            messages = await handle_fetch_messages(
                user=27,                 # user=self.user,
                chat_room=self.chat_room,
                data=data
            )
            
            if messages:
                messageslist = []
                for mgs in messages:
                    print(f"message is available = {mgs.text}")
                    serialized_message = await serialize_message(mgs)
                    messageslist.append(serialized_message)
                    print(f"serialized message = {serialized_message}")
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "fetch_messages",
                            "message": "message sent successfully!",
                            "data": messageslist
                        }
                        )
                    )
            
        elif action == "edit_message":
            edited_message = await handle_edit_message(data=data)
            if edited_message:
                serialized_message = await serialize_message(edited_message)
                await self.send(
                        text_data=json.dumps(
                            {
                                "message": "message edited successfully",
                                "data": serialized_message,
                            }
                        )
                    )
                
        elif action == "message_read":
            read_messages = await handle_mark_messages_read(data=data)
            read_messages_list = []
            
            if read_messages:
                for msg in read_messages:
                    serialized_message = await serialize_message(msg)
                    read_messages_list.append(serialized_message)
                    
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "message_read",
                            "message": "message read successfully!",
                            "data": read_messages_list
                        }
                    )
                )
            else:
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "message_read",
                            "message": "No unread messages found!",
                        }
                    )
                )
                
        elif action == "delete_message":
            await handle_delete_message(data, on_message_deleted=lambda msg_id:  
                print('MESSAGES DELETED SUCCESSFULLY.')
            )
            
        elif action == "react_to_message":
            reacted_message = await handle_react_to_message(data=data)
            if reacted_message:
                serialized_message = await serialize_message(reacted_message)
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "react_to_message",
                            "message": "message reacted successfully",
                            "data": serialized_message,
                        }
                    )
                )
            else:
                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "react_to_message",
                            "message": "message reaction failed",
                        }
                    )
                )
