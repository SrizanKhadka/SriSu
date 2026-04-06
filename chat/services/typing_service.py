from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.models import ChatRoom
from chat.selectors.chat_room_selectors import get_chat_room_for_user
from chat.websocket.exceptions import ChatRoomNotFoundError


@dataclass(frozen=True)
class TypingResult:
    chat_room_id: str
    typing_users: dict[str, bool]


def set_typing_status(
    *,
    user: UserModel,
    chat_room_id: str,
    is_typing: bool,
) -> TypingResult:
    """
    Set or clear typing state for the authenticated user in a chat room.

    Rules:
    - user must belong to the room
    - room row is locked during update to prevent concurrent overwrites
    - typing state is stored as a lightweight JSON map keyed by user id
    """
    chat_room = get_chat_room_for_user(chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")

    with transaction.atomic():
        locked_room = ChatRoom.objects.select_for_update().get(id=chat_room.id)

        typing_data = dict(locked_room.is_typing or {})
        user_key = str(user.id)

        if is_typing:
            typing_data[user_key] = True
        else:
            typing_data.pop(user_key, None)

        locked_room.is_typing = typing_data
        locked_room.save(update_fields=["is_typing", "updated_at"])

    return TypingResult(
        chat_room_id=str(chat_room.id),
        typing_users=typing_data,
    )