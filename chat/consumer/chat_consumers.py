import json
import traceback
from chat.utils.chatutils import *
from channels.generic.websocket import AsyncWebsocketConsumer
from .message_sender import handle_send_message
from .message_retriever import get_messages_before
from .message_editor import (
    handle_edit_message,
    handle_mark_messages_read,
    handle_react_to_message,
    handle_mark_messages_delivered,
)
from .message_deleter import handle_delete_message
from .chat_room_operations import get_and_serialize_chat_rooms, set_user_typing


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("INSIDE CONNECT METHOD")

        # Initialize attributes to avoid AttributeError in disconnect
        self.room_group_name = None
        self.chat_room = None

        # Check if user is authenticated
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            print("User is anonymous, closing connection")
            await self.close()
            return

        self.chat_room_id = self.scope["url_route"]
        self.room_group_name = f"chat_websocket_room"
        # self.chat_room = await get_chat_room(self.chat_room_id)

        # print(f"Chat room ID: {self.chat_room_id}")
        # print(f"Chat room: {self.chat_room}")

        # if not self.chat_room:
        #     print("Chat room not found, closing connection")
        #     await self.close()
        #     return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            action = data.get("action")


            print("INSIDE ON RECEIVE METHOD", data)

            if action == "send_message":
                chat_room_id = data.get("chat_room")
                chat_room = await get_chat_room(chat_room_id)
                message, chat_room = await handle_send_message(self.scope,data, chat_room=chat_room)
                if message:
                    serialized_message = await serialize_message(message, self.scope)
                    # BROADCAST to all clients in the room group
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "send_message",
                            "message": "message_sent successfully",
                            "data": serialized_message,
                            "updated_chat_room": chat_room
                        }
                    )
                else:
                    await self.send(
                        text_data=json.dumps(
                            {
                                "message": "message_sent failed",
                            }
                        )
                    )

            elif action == "fetch_messages":
                before_id = data.get("page")
                limit = data.get("page_size", 20)
                chat_room = data.get("chat_room_id")
                print("FETCH MESSAGES USER:", self.scope.get("user"))

                result = await get_messages_before(
                    scope=self.scope,
                    chat_room=chat_room,
                    user=self.scope.get("user"),
                    page=before_id,
                    limit=limit,
                )

                # print("FETCH MESSAGES RESULT:", result)

                await self.send(
                    text_data=json.dumps(
                        {
                            "action": "fetch_messages",
                            "message": (
                                "Messages fetched successfully"
                                if result["messages"]
                                else "No messages found"
                            ),
                            "data": result,
                            "success": True,
                        }
                    )
                )

            elif action == "edit_message":
                edited_message = await handle_edit_message(data=data)
                if edited_message:
                    serialized_message = await serialize_message(
                        edited_message, self.scope
                    )

                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "edit_message",
                            "message": "message edited successfully",
                            "data": serialized_message,
                        },
                    )

            elif action == "message_read":
                read_payload = await handle_mark_messages_read(
                    data=data,
                )

                if not read_payload:
                    await self.send(
                        text_data=json.dumps(
                            {
                                "action": "message_read",
                                "message": "No unread messages found!",
                            }
                        )
                    )
                    return

                # Broadcast read receipt to all users in the room
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "action": "message_read",
                        "message": "Messages marked as read",
                        "data": read_payload,
                    },
                )

            elif action == "delete_message":
                updated_message = await handle_delete_message(data)

                serialized_message = await serialize_message(
                    updated_message, self.scope
                )
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "action": "delete_message",
                        "message": "message deleted successfully",
                        "data": serialized_message,
                    },
                )

            elif action == "react_to_message":
                reacted_message = await handle_react_to_message(data=data)
                if reacted_message:
                    serialized_message = await serialize_message(
                        reacted_message, self.scope
                    )

                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "action": "react_to_message",
                            "message": "message reacted successfully",
                            "data": serialized_message,
                        },
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

            # Inside receive:
            elif action == "typing":

                is_typing_flag = data.get("is_typing", False)
                user_id = data.get("user_id")
                chat_room_id = data.get("chat_room_id")

                # Update the typing status in DB
                updated_typing_data = await set_user_typing(
                    chat_room_id, user_id, is_typing_flag
                )
                print(f"Updated typing data: {updated_typing_data}")

                # Broadcast typing info to all users in room except sender
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "action": "typing",
                        "message": f"{'started' if is_typing_flag else 'stopped'} typing",
                        "data": {
                            "typing_users": updated_typing_data,
                            "chat_room_id": chat_room_id
                        },
                    },
                )

            elif action == "message_delivered":
                delivered_payload = await handle_mark_messages_delivered(
                    data=data,
                )

                if not delivered_payload:
                    return

                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "action": "message_delivered",
                        "message": "Messages delivered",
                        "data": delivered_payload,
                    },
                )

            elif action == "get_chat_rooms":
                user = self.scope["user"]
                limit = int(data.get("limit", 20))
                last_updated = data.get("last_updated")  # ISO string from client, optional
                
                print("Inside get_chat_rooms action")

                response = await get_and_serialize_chat_rooms(
                    user,
                    limit=limit,
                    last_updated=last_updated,
                    scope=self.scope,
                )

                await self.send(text_data=json.dumps({
                    "action": "get_chat_rooms",
                    "data": response
                }))
            
            elif action == "chat_room_updated":
                pass
            else:
                await self.send(
                    text_data=json.dumps(
                        {
                            "message": f"Unknown action: {action}",
                        }
                    )
                )


        except Exception as e:
            print(f"Error in receive: {e}")
            traceback.print_exc()

    async def chat_message(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "action": event["action"],
                    "message": event["message"],
                    "data": event.get("data"),
                    "updated_chat_room": event.get("updated_chat_room"),
                }
            )
        )
