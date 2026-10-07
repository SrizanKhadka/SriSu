from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.services.authorization import lock_authorized_room, require_locked_legacy_session
from chat.websocket.exceptions import ChatRoomNotFoundError, InvalidMessagePayloadError


@dataclass(frozen=True)
class TypingResult:
    chat_room_id: str
    typing_users: dict[str, bool]


def set_typing_status(
    *,
    user: UserModel,
    chat_room_id: str,
    is_typing: bool,
    device_session_id=None,
) -> TypingResult:
    """
    Set or clear typing state for the authenticated user in a chat room.

    Rules:
    - user must belong to the room
    - room row is locked during update to prevent concurrent overwrites
    - typing state is stored as a lightweight JSON map keyed by user id
    """
    with transaction.atomic():
        locked_room = lock_authorized_room(chat_room_id, user)
        if not locked_room:
            raise ChatRoomNotFoundError("Chat room not found or access denied.")
        require_locked_legacy_session(user, device_session_id)
        if locked_room.encrypted_v2_started_at is not None:
            raise InvalidMessagePayloadError(
                "This room requires the chat v2 protocol."
            )

        typing_data = dict(locked_room.is_typing or {})
        user_key = str(user.id)

        if is_typing:
            typing_data[user_key] = True
        else:
            typing_data.pop(user_key, None)

        locked_room.is_typing = typing_data
        locked_room.save(update_fields=["is_typing", "updated_at"])

    return TypingResult(
        chat_room_id=str(locked_room.id),
        typing_users=typing_data,
    )
