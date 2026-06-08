from __future__ import annotations

from typing import Optional

from django.db.models import Q, QuerySet

from chat.models import MessageModel, ChatRoom
from authentication.models import UserModel
from utils.choices import DeleteOption


def get_room_messages_queryset(chat_room: ChatRoom) -> QuerySet[MessageModel]:
    """
    Base queryset for all messages in a room.
    """
    return (
        MessageModel.objects
        .filter(chat_room=chat_room)
        .select_related(
            "chat_room",
            "sender",
            "receiver",
            "reply_to",
            "reply_to__sender",
        )
         .prefetch_related(
            "medias",
            "reaction_records",
            "reaction_records__user",
        )
        .order_by("-id")
    )


def get_visible_room_messages_queryset(
    chat_room: ChatRoom,
    user: UserModel,
) -> QuerySet[MessageModel]:
    """
    Messages visible to a user after applying per-user deletion rules.
    """
    return (
        get_room_messages_queryset(chat_room)
        .exclude(
            deletions__user=user,
            deletions__delete_option__in=[
                DeleteOption.DELETE_FOR_ME,
                DeleteOption.CONVERSATION_DELETED,
            ],
        )
        .distinct()
    )


def get_paginated_messages_before(
    chat_room: ChatRoom,
    user: UserModel,
    cursor: Optional[int] = None,
    limit: int = 20,
) -> tuple[list[MessageModel], bool, Optional[int]]:
    """
    Cursor-based pagination for room messages.
    """
    queryset = get_visible_room_messages_queryset(chat_room, user)

    if cursor:
        queryset = queryset.filter(id__lt=cursor)

    rows = list(queryset[: limit + 1])

    has_more = len(rows) > limit
    messages = rows[:limit]
    next_cursor = messages[-1].id if messages else None

    return messages, has_more, next_cursor


def get_message_by_id(message_id: int) -> Optional[MessageModel]:
    """
    Internal selector for trusted backend-only use.
    """
    return (
        MessageModel.objects
        .select_related(
            "chat_room",
            "sender",
            "receiver",
            "reply_to",
            "reply_to__sender",
        )
        .prefetch_related("medias")
        .filter(id=message_id)
        .first()
    )


def get_room_message_for_user(
    message_id: int,
    user: UserModel,
) -> Optional[MessageModel]:
    """
    Fetch a message only if the user belongs to the message's room.
    """
    return (
        MessageModel.objects
        .select_related(
            "chat_room",
            "sender",
            "receiver",
            "reply_to",
            "reply_to__sender",
        )
        .prefetch_related("medias")
        .filter(id=message_id)
        .filter(Q(chat_room__user_one=user) | Q(chat_room__user_two=user))
        .first()
    )


def get_reply_target_for_room(
    reply_to_id: int,
    chat_room: ChatRoom,
) -> Optional[MessageModel]:
    """
    Fetch a reply target only if it belongs to the same room.
    """
    return (
        MessageModel.objects
        .select_related("sender")
        .prefetch_related("medias")
        .filter(id=reply_to_id, chat_room=chat_room)
        .first()
    )