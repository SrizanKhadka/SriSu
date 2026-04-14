from __future__ import annotations

from channels.db import database_sync_to_async

from chat.selectors.chat_room_selectors import get_chat_room_for_user, get_chat_rooms_for_user
from chat.selectors.message_selectors import get_paginated_messages_before
from chat.services.message_service import (
    DeleteMessageInput,
    EditMessageInput,
    SendMessageInput,
    delete_message,
    edit_message,
    send_message,
)
from chat.services.receipt_service import mark_messages_delivered, mark_messages_read
from chat.services.reaction_service import react_to_message
from chat.services.typing_service import set_typing_status
from chat.websocket.actions import ChatSocketActions
from chat.websocket.events import ChatSocketEvents
from chat.websocket.exceptions import ChatServiceError
from chat.websocket.responses import socket_error, socket_event, socket_success


class ChatSocketHandlerMixin:
    async def handle_action(self, action: str, payload: dict, request_id: str | None = None):
        handlers = {
            ChatSocketActions.SEND_MESSAGE: self._handle_send_message,
            ChatSocketActions.FETCH_MESSAGES: self._handle_fetch_messages,
            ChatSocketActions.EDIT_MESSAGE: self._handle_edit_message,
            ChatSocketActions.DELETE_MESSAGE: self._handle_delete_message,
            ChatSocketActions.MARK_READ: self._handle_mark_read,
            ChatSocketActions.MARK_DELIVERED: self._handle_mark_delivered,
            ChatSocketActions.REACT_TO_MESSAGE: self._handle_react_to_message,
            ChatSocketActions.SET_TYPING: self._handle_set_typing,
            ChatSocketActions.GET_CHAT_ROOMS: self._handle_get_chat_rooms,
        }

        handler = handlers.get(action)
        if not handler:
            return socket_error(
                action=action,
                message=f"Unknown action: {action}",
                request_id=request_id,
            )

        try:
            return await handler(payload=payload, request_id=request_id)
        except ChatServiceError as exc:
            return socket_error(
                action=action,
                message=str(exc),
                request_id=request_id,
            )
        except Exception:
            return socket_error(
                action=action,
                message="Internal server error",
                request_id=request_id,
            )

    async def _handle_send_message(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]

        message = await database_sync_to_async(send_message)(
            user=user,
            payload=SendMessageInput(
                chat_room_id=payload["chat_room_id"],
                text=payload.get("text", ""),
                message_type=payload.get("message_type", "text"),
                media_ids=payload.get("media_ids", []),
                reply_to_id=payload.get("reply_to_id"),
                media_url=payload.get("media_url"),
                sticker_url=payload.get("sticker_url"),
            ),
        )

        message_data = await self.serialize_message(message)
        room_payload = await self.serialize_chat_room_preview(message.chat_room)

        await self.broadcast_to_room(
            chat_room_id=str(message.chat_room_id),
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_CREATED,
                message="Message created",
                data={
                    "message": message_data,
                    "chat_room": room_payload,
                },
            ),
        )

        await self.broadcast_chat_room_update_to_participants(
            message.chat_room,
            room_payload,
        )

        return socket_success(
            action=ChatSocketActions.SEND_MESSAGE,
            message="Message sent successfully",
            data={
                "message": message_data,
                "chat_room": room_payload,
            },
            request_id=request_id,
        )

    async def _handle_fetch_messages(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        chat_room = await database_sync_to_async(get_chat_room_for_user)(
            payload["chat_room_id"],
            user,
        )
        
        if not chat_room:
            return socket_error(
                action=ChatSocketActions.FETCH_MESSAGES,
                message="Chat room not found or access denied",
                request_id=request_id,
            )
            
        await self.ensure_room_subscription(str(chat_room.id))

        messages, has_more, next_cursor = await database_sync_to_async(get_paginated_messages_before)(
            chat_room=chat_room,
            user=user,
            cursor=payload.get("cursor"),
            limit=int(payload.get("limit", 20)),
        )

        serialized_messages = [await self.serialize_message(msg) for msg in messages]

        return socket_success(
            action=ChatSocketActions.FETCH_MESSAGES,
            message="Messages fetched successfully",
            data={
                "chat_room_id": str(chat_room.id),
                "messages": serialized_messages,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
            request_id=request_id,
        )

    async def _handle_edit_message(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        message = await database_sync_to_async(edit_message)(
            user=user,
            payload=EditMessageInput(
                message_id=payload["message_id"],
                text=payload["text"],
            ),
        )

        message_data = await self.serialize_message(message)

        await self.broadcast_to_room(
            chat_room_id=str(message.chat_room_id),
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_UPDATED,
                message="Message edited",
                data={"message": message_data},
            ),
        )

        return socket_success(
            action=ChatSocketActions.EDIT_MESSAGE,
            message="Message edited successfully",
            data={"message": message_data},
            request_id=request_id,
        )

    async def _handle_delete_message(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        message = await database_sync_to_async(delete_message)(
            user=user,
            payload=DeleteMessageInput(
                message_id=payload["message_id"],
                delete_option=payload["delete_option"],
            ),
        )

        message_data = await self.serialize_message(message)
        print("Deleted message data:", message_data)
        

        await self.broadcast_to_room(
            chat_room_id=str(message.chat_room_id),
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_DELETED,
                message="Message deleted",
                data={"message": message_data},
            ),
        )

        return socket_success(
            action=ChatSocketActions.DELETE_MESSAGE,
            message="Message deleted successfully",
            data={"message": message_data},
            request_id=request_id,
        )

    async def _handle_mark_read(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        result = await database_sync_to_async(mark_messages_read)(
            user=user,
            chat_room_id=payload["chat_room_id"],
        )

        if result is None:
            return socket_success(
                action=ChatSocketActions.MARK_READ,
                message="No unread messages found",
                data={
                    "chat_room_id": payload["chat_room_id"],
                    "message_ids": [],
                },
                request_id=request_id,
            )

        event_payload = {
            "chat_room_id": result.chat_room_id,
            "read_by": result.user_id,
            "message_ids": result.message_ids,
        }

        await self.broadcast_to_room(
            chat_room_id=result.chat_room_id,
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_READ,
                message="Messages marked as read",
                data=event_payload,
            ),
        )

        return socket_success(
            action=ChatSocketActions.MARK_READ,
            message="Messages marked as read",
            data=event_payload,
            request_id=request_id,
        )

    async def _handle_mark_delivered(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        result = await database_sync_to_async(mark_messages_delivered)(
            user=user,
            chat_room_id=payload["chat_room_id"],
        )

        if result is None:
            return socket_success(
                action=ChatSocketActions.MARK_DELIVERED,
                message="No pending messages found",
                data={
                    "chat_room_id": payload["chat_room_id"],
                    "message_ids": [],
                },
                request_id=request_id,
            )

        event_payload = {
            "chat_room_id": result.chat_room_id,
            "delivered_to": result.user_id,
            "message_ids": result.message_ids,
        }

        await self.broadcast_to_room(
            chat_room_id=result.chat_room_id,
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_DELIVERED,
                message="Messages delivered",
                data=event_payload,
            ),
        )

        return socket_success(
            action=ChatSocketActions.MARK_DELIVERED,
            message="Messages delivered",
            data=event_payload,
            request_id=request_id,
        )

    async def _handle_react_to_message(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        message, reaction_result = await database_sync_to_async(react_to_message)(
            user=user,
            message_id=payload["message_id"],
            reaction=payload["reaction"],
        )

        event_payload = {
            "message_id": reaction_result.message_id,
            "user_id": reaction_result.user_id,
            "reaction": reaction_result.reaction,
            "was_removed": reaction_result.was_removed,
        }

        await self.broadcast_to_room( #why this is not working? 
            chat_room_id=str(message.chat_room_id),
            event=socket_event(
                action=ChatSocketEvents.MESSAGE_REACTED,
                message="Message reaction updated",
                data=event_payload,
            ),
        )

        return socket_success(
            action=ChatSocketActions.REACT_TO_MESSAGE,
            message="Reaction updated successfully",
            data=event_payload,
            request_id=request_id,
        )

    async def _handle_set_typing(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        
        self.ensure_room_subscription(str(payload["chat_room_id"]))
        
        result = await database_sync_to_async(set_typing_status)(
            user=user,
            chat_room_id=payload["chat_room_id"],
            is_typing=bool(payload.get("is_typing", False)),
        )

        event_payload = {
            "chat_room_id": result.chat_room_id,
            "typing_users": result.typing_users,
        }

        await self.broadcast_to_room(
            chat_room_id=result.chat_room_id,
            event=socket_event(
                action=ChatSocketEvents.TYPING_UPDATED,
                message="Typing state updated",
                data=event_payload,
            ),
        )

        return socket_success(
            action=ChatSocketActions.SET_TYPING,
            message="Typing state updated",
            data=event_payload,
            request_id=request_id,
        )

    async def _handle_get_chat_rooms(self, *, payload: dict, request_id: str | None):
        user = self.scope["user"]
        rooms = await database_sync_to_async(get_chat_rooms_for_user)(
            user=user,
            limit=int(payload.get("limit", 20)),
            last_updated=payload.get("last_updated"),
        )

        serialized_rooms = []
        for room in rooms:
            serialized_rooms.append(await self.serialize_chat_room_list_item(room))

        next_cursor = rooms[-1].updated_at.isoformat() if rooms else None

        return socket_success(
            action=ChatSocketActions.GET_CHAT_ROOMS,
            message="Chat rooms fetched successfully",
            data={
                "chat_rooms": serialized_rooms,
                "next_cursor": next_cursor,
                "limit": int(payload.get("limit", 20)),
            },
            request_id=request_id,
        )