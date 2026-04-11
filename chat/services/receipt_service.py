from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.models import MessageModel
from chat.selectors.chat_room_selectors import get_chat_room_for_user
from chat.websocket.exceptions import ChatRoomNotFoundError


@dataclass(frozen=True)
class ReceiptResult:
    chat_room_id: str
    user_id: int
    message_ids: list[int]


def mark_messages_delivered(
    *,
    user: UserModel,
    chat_room_id: str,
) -> ReceiptResult | None:
    """
    Mark all pending incoming messages in the room as delivered for the authenticated user.

    Rules:
    - user must belong to the room
    - only messages received by this user are affected
    - already delivered or deleted messages are skipped
    """
    chat_room = get_chat_room_for_user(chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")

    pending_messages = list(
        MessageModel.objects.filter(
            chat_room=chat_room,
            receiver=user,
            is_delivered=False,
            is_deleted=False,
        ).only("id", "is_delivered")
    )

    if not pending_messages:
        return None

    for message in pending_messages:
        message.is_delivered = True

    MessageModel.objects.bulk_update(
        pending_messages,
        ["is_delivered"],
    )

    return ReceiptResult(
        chat_room_id=str(chat_room.id),
        user_id=user.id,
        message_ids=[message.id for message in pending_messages],
    )


def mark_messages_read(
    *,
    user: UserModel,
    chat_room_id: str,
) -> ReceiptResult | None:
    """
    Mark all unread incoming messages in the room as read for the authenticated user.

    Rules:
    - user must belong to the room
    - only messages received by this user are affected
    - already read or deleted messages are skipped
    - room unread count is reset for this user
    """
    chat_room = get_chat_room_for_user(chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")

    unread_messages = list(
        MessageModel.objects.filter(
            chat_room=chat_room,
            receiver=user,
            is_read=False,
            is_deleted=False,
        ).only("id", "is_read")
    )

    if not unread_messages:
        return None

    with transaction.atomic():
        for message in unread_messages:
            message.is_read = True
            message.is_delivered = True

        MessageModel.objects.bulk_update(
            unread_messages,
            ["is_read", "is_delivered"],
        )

    return ReceiptResult(
        chat_room_id=str(chat_room.id),
        user_id=user.id,
        message_ids=[message.id for message in unread_messages],
    )