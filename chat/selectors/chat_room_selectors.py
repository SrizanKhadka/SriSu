from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from chat.models import ChatRoom, MessageModel
from authentication.models import UserModel


def get_user_chat_rooms_queryset(user: UserModel) -> QuerySet[ChatRoom]:
    """
    Base queryset for all chat rooms the user participates in.

    Read-only selector:
    - fetches only rooms where the user is a participant
    - preloads related users and last_message for efficient room-list rendering
    """
    return (
        ChatRoom.objects
        .filter(Q(user_one=user) | Q(user_two=user))
        .select_related(
            "user_one",
            "user_two",
            "couple",
            "singles",
            "last_message",
            "last_message__sender",
            "last_message__receiver",
            "last_message__reply_to",
            "last_message__reply_to__sender",
        )
        .prefetch_related(
            "last_message__medias",
        )
        .order_by("-updated_at")
    )


def get_chat_rooms_for_user(
    user: UserModel,
    limit: int = 20,
    last_updated: Optional[str] = None,
) -> list[ChatRoom]:
    """
    Cursor-based room list selector.

    Args:
        user: authenticated user
        limit: page size
        last_updated: ISO timestamp cursor; fetch rooms older than this cursor

    Returns:
        List of ChatRoom instances ordered by most recently updated first.
    """
    
    queryset = get_user_chat_rooms_queryset(user)

    cursor_dt = _parse_iso_datetime(last_updated)
    if cursor_dt:
        queryset = queryset.filter(updated_at__lt=cursor_dt)

    return list(queryset[:limit])


def get_chat_room_for_user(
    chat_room_id: str,
    user: UserModel,
) -> Optional[ChatRoom]:
    """
    Fetch a single room only if the given user is a participant.

    This is the main authorization-safe room selector for websocket actions.
    """
    return (
        ChatRoom.objects
        .filter(id=chat_room_id)
        .filter(Q(user_one=user) | Q(user_two=user))
        .select_related("user_one", "user_two", "couple", "singles", "last_message")
        .first()
    )


def get_chat_room_by_id(chat_room_id: str) -> Optional[ChatRoom]:
    """
    Internal selector for trusted backend-only use cases.

    Prefer get_chat_room_for_user() for user-driven websocket/API actions.
    """
    return (
        ChatRoom.objects
        .select_related("user_one", "user_two", "couple", "singles", "last_message")
        .filter(id=chat_room_id)
        .first()
    )


def get_other_user(chat_room: ChatRoom, current_user: UserModel) -> Optional[UserModel]:
    """
    Return the other participant in a 1-to-1 chat room.
    """
    if chat_room.user_one_id == current_user.id:
        return chat_room.user_two
    if chat_room.user_two_id == current_user.id:
        return chat_room.user_one
    return None


def is_user_in_chat_room(chat_room: ChatRoom, user: UserModel) -> bool:
    """
    Lightweight room membership check.
    """
    return user.id in {chat_room.user_one_id, chat_room.user_two_id}


def get_room_unread_count_for_user(chat_room: ChatRoom, user: UserModel) -> int:
    """
    Read-only unread count for one user in one room.

    This does NOT write to chat_room.unread_count.
    Service layer should decide when persisted unread_count needs updating.
    """
    return (
        MessageModel.objects
        .filter(
            chat_room=chat_room,
            receiver=user,
            is_read=False,
            is_deleted=False,
        )
        .exclude(
            deletions__user=user,
        )
        .count()
    )


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Safely parse an ISO datetime string into a timezone-aware datetime.
    Returns None for invalid input.
    """
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())

    return parsed