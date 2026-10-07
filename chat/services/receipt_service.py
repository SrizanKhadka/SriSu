from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.models import MessageModel
from chat.services.authorization import lock_authorized_room, require_locked_legacy_session
from chat.websocket.exceptions import ChatRoomNotFoundError, InvalidMessagePayloadError


@dataclass(frozen=True)
class ReceiptResult:
    chat_room_id: str
    user_id: int
    message_ids: list[int]


@transaction.atomic
def mark_messages_delivered(
    *,
    user: UserModel,
    chat_room_id: str,
    device_session_id=None,
) -> ReceiptResult | None:
    """
    Mark all pending incoming messages in the room as delivered for the authenticated user.

    Rules:
    - user must belong to the room
    - only messages received by this user are affected
    - already delivered or deleted messages are skipped
    """
    chat_room = lock_authorized_room(chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")
    require_locked_legacy_session(user, device_session_id)
    if chat_room.encrypted_v2_started_at is not None:
        raise InvalidMessagePayloadError(
            "This room requires chat v2 receipt cursors."
        )

    pending_messages = list(
        MessageModel.objects.filter(
            chat_room=chat_room,
            receiver=user,
            is_delivered=False,
            is_deleted=False,
            legacy_plaintext=True,
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


@transaction.atomic
def mark_messages_read(
    *,
    user: UserModel,
    chat_room_id: str,
    device_session_id=None,
) -> ReceiptResult | None:
    """
    Mark all unread incoming messages in the room as read for the authenticated user.

    Rules:
    - user must belong to the room
    - only messages received by this user are affected
    - already read or deleted messages are skipped
    - room unread count is reset for this user
    """
    chat_room = lock_authorized_room(chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")
    require_locked_legacy_session(user, device_session_id)
    if chat_room.encrypted_v2_started_at is not None:
        raise InvalidMessagePayloadError(
            "This room requires chat v2 receipt cursors."
        )

    unread_messages = list(
        MessageModel.objects.filter(
            chat_room=chat_room,
            receiver=user,
            is_read=False,
            is_deleted=False,
            legacy_plaintext=True,
        ).only("id", "is_read")
    )

    if not unread_messages:
        return None

    for message in unread_messages:
        message.is_read = True
        message.is_delivered = True

    MessageModel.objects.bulk_update(
        unread_messages,
        ["is_read", "is_delivered"],
    )

    chat_room.unread_count[str(user.id)] = 0
    chat_room.save(update_fields=["unread_count", "updated_at"])

    return ReceiptResult(
        chat_room_id=str(chat_room.id),
        user_id=user.id,
        message_ids=[message.id for message in unread_messages],
    )
