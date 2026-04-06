from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.db.models import Q

from authentication.models import UserModel
from chat.models import ChatRoom, MessageModel


def update_room_after_message_created(
    *,
    chat_room: ChatRoom,
    message: MessageModel,
) -> ChatRoom:
    """
    Update room metadata after a new message is created.

    Responsibilities:
    - set last_message
    - increment unread count for the receiver
    - persist room changes

    Notes:
    - updated_at will be handled automatically by auto_now on save()
    """
    unread_count = dict(chat_room.unread_count or {})

    receiver = message.receiver
    if receiver:
        receiver_key = str(receiver.id)
        unread_count[receiver_key] = unread_count.get(receiver_key, 0) + 1

    chat_room.last_message = message
    chat_room.unread_count = unread_count
    chat_room.save(update_fields=["last_message", "unread_count", "updated_at"])

    return chat_room


def update_room_after_message_created(
    *,
    chat_room: ChatRoom,
    message: MessageModel,
) -> ChatRoom:
    with transaction.atomic():
        locked_room = ChatRoom.objects.select_for_update().get(id=chat_room.id)

        unread_count = dict(locked_room.unread_count or {})
        receiver = message.receiver

        if receiver:
            receiver_key = str(receiver.id)
            unread_count[receiver_key] = unread_count.get(receiver_key, 0) + 1

        locked_room.last_message = message
        locked_room.unread_count = unread_count
        locked_room.save(update_fields=["last_message", "unread_count", "updated_at"])

        return locked_room


def recalculate_room_unread_count(chat_room: ChatRoom) -> ChatRoom:
    """
    Recalculate unread counts for both participants from database state.

    This is useful as a repair/sync function, not as the default path for every read request.
    """
    unread_count: dict[str, int] = {}

    for participant in [chat_room.user_one, chat_room.user_two]:
        if not participant:
            continue

        unread_count[str(participant.id)] = (
            MessageModel.objects
            .filter(
                chat_room=chat_room,
                receiver=participant,
                is_read=False,
                is_deleted=False,
            )
            .count()
        )

    chat_room.unread_count = unread_count
    chat_room.save(update_fields=["unread_count", "updated_at"])
    return chat_room


def update_room_last_message_if_needed(chat_room: ChatRoom) -> ChatRoom:
    """
    Repair/sync the room last_message pointer from message history.

    Use this after destructive operations like delete-for-everyone if the deleted message
    was the room's current last_message and you want to re-evaluate room preview state.
    """
    last_message = (
        MessageModel.objects
        .filter(chat_room=chat_room)
        .order_by("-timestamp", "-id")
        .first()
    )

    chat_room.last_message = last_message
    chat_room.save(update_fields=["last_message", "updated_at"])
    return chat_room


def mark_room_as_active(chat_room: ChatRoom) -> ChatRoom:
    """
    Touch the room so updated_at refreshes even when no other room field changes.

    Useful for future cases like pin/unpin or other room-level state changes.
    """
    chat_room.save(update_fields=["updated_at"])
    return chat_room