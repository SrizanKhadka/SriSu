from __future__ import annotations

from channels.db import database_sync_to_async

from chat.presenters.message_presenter import serialize_message_for_socket_sync
from chat.selectors.chat_room_selectors import get_other_user
from chat.selectors.message_selectors import get_visible_room_messages_queryset
from utils.choices import DeleteOption
from utils.helpers import get_base_url

def _serialize_other_user(other_user, base_url: str) -> dict | None:
    if not other_user:
        return None

    profile_photo = getattr(other_user, "profile_photo", None)
    profile_photo_url = None


    if profile_photo:
        try:
            profile_photo_url = f"{base_url}{profile_photo.url}"
        except Exception:
            profile_photo_url = None

    return {
        "id": other_user.id,
        "full_name": getattr(other_user, "full_name", None),
        "username": getattr(other_user, "username", None),
        "profile_photo": profile_photo_url,
    }


def serialize_chat_room_preview_for_socket_sync(chat_room, me, scope) -> dict:
    """
    Serialize a room preview payload for websocket responses.
    """

    base_url = get_base_url(scope)
    other_user = get_other_user(chat_room, me)
    last_message = chat_room.last_message
    if last_message is not None:
        viewer_deletions = getattr(last_message, "_viewer_deletions", None)
        hidden = bool(viewer_deletions) if viewer_deletions is not None else (
            last_message.deletions.filter(user=me).exists()
        )
        if (
            hidden
            or not last_message.legacy_plaintext
            or last_message.delete_option == DeleteOption.DELETE_FOR_ME
        ):
            last_message = get_visible_room_messages_queryset(chat_room, me).first()

    return {
        "id": str(chat_room.id),
        "chat_type": chat_room.chat_type,
        "user_one_id": chat_room.user_one_id,
        "user_two_id": chat_room.user_two_id,
        "user": _serialize_other_user(me, base_url),
        "other_user": _serialize_other_user(other_user, base_url),
        "last_message": (
            serialize_message_for_socket_sync(last_message, scope)
            if last_message and last_message.legacy_plaintext
            else None
        ),
        "unread_count": chat_room.unread_count or {},
        "is_typing": chat_room.is_typing or {},
        "updated_at": chat_room.updated_at.isoformat(),
        "created_at": chat_room.created_at.isoformat(),
    }


@database_sync_to_async
def serialize_chat_room_preview_for_socket(chat_room, me, scope) -> dict:
    return serialize_chat_room_preview_for_socket_sync(chat_room, me, scope)


@database_sync_to_async
def serialize_chat_room_list_item_for_socket(chat_room, me, scope) -> dict:
    return serialize_chat_room_preview_for_socket_sync(chat_room, me, scope)
