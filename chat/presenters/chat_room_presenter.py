from __future__ import annotations

from asgiref.sync import sync_to_async

from chat.presenters.message_presenter import serialize_message_for_socket_sync
from chat.selectors.chat_room_selectors import get_other_user
from utils.helpers import get_base_url

def _serialize_other_user(other_user, base_url: str) -> dict | None:
    if not other_user:
        return None

    profile_photo = getattr(other_user, "profile_photo", None)
    profile_photo_url = None

    print(f"Serializing other user: {other_user}, profile_photo: {profile_photo}, base_url: {base_url}")

    if profile_photo:
        try:
            profile_photo_url = f"{base_url}{profile_photo.url}"
        except Exception:
            profile_photo_url = None

    return {
        "id": other_user.id,
        "full_name": getattr(other_user, "full_name", None),
        "phone_number": getattr(other_user, "phone_number", None),
        "profile_photo": profile_photo_url,
    }


def serialize_chat_room_preview_for_socket_sync(chat_room, me, scope) -> dict:
    """
    Serialize a room preview payload for websocket responses.
    """

    base_url = get_base_url(scope)
    other_user = get_other_user(chat_room, me)

    return {
        "id": str(chat_room.id),
        "chat_type": chat_room.chat_type,
        "user_one_id": chat_room.user_one_id,
        "user_two_id": chat_room.user_two_id,
        "user": _serialize_other_user(me, base_url),
        "other_user": _serialize_other_user(other_user, base_url),
        "last_message": (
            serialize_message_for_socket_sync(chat_room.last_message, scope)
            if chat_room.last_message
            else None
        ),
        "unread_count": chat_room.unread_count or {},
        "is_typing": chat_room.is_typing or {},
        "updated_at": chat_room.updated_at.isoformat(),
        "created_at": chat_room.created_at.isoformat(),
    }


@sync_to_async
def serialize_chat_room_preview_for_socket(chat_room, me, scope) -> dict:
    return serialize_chat_room_preview_for_socket_sync(chat_room, me, scope)


@sync_to_async
def serialize_chat_room_list_item_for_socket(chat_room, me, scope) -> dict:
    return serialize_chat_room_preview_for_socket_sync(chat_room, me, scope)