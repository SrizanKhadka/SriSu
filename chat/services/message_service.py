from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from authentication.models import UserModel
from chat.models import ChatRoom, MediaModel, MessageDeletion, MessageModel
from chat.selectors.chat_room_selectors import get_chat_room_for_user, get_other_user
from chat.selectors.message_selectors import (
    get_message_by_id,
    get_reply_target_for_room,
    get_room_message_for_user,
)
from chat.websocket.exceptions import (
    ChatRoomNotFoundError,
    InvalidDeleteOperationError,
    InvalidMessagePayloadError,
    InvalidReplyTargetError,
    MessageNotFoundError,
    PermissionDeniedError,
)
from utils.choices import DeleteOption, MessageType
from chat.services.chat_room_service import update_room_after_message_created


@dataclass(frozen=True)
class SendMessageInput:
    chat_room_id: str
    text: str = ""
    message_type: str = MessageType.TEXT
    media_ids: Optional[list[int]] = None
    reply_to_id: Optional[int] = None
    media_url: Optional[str] = None
    sticker_url: Optional[str] = None


@dataclass(frozen=True)
class EditMessageInput:
    message_id: int
    text: str


@dataclass(frozen=True)
class DeleteMessageInput:
    message_id: int
    delete_option: str


def send_message(*, user: UserModel, payload: SendMessageInput) -> MessageModel:
    """
    Create and persist a new message in a room.

    Rules:
    - authenticated user must belong to the room
    - sender is always the authenticated user
    - receiver is derived from the room
    - reply target must belong to the same room
    - message must contain at least one meaningful content source
    """
    chat_room = get_chat_room_for_user(payload.chat_room_id, user)
    if not chat_room:
        raise ChatRoomNotFoundError("Chat room not found or access denied.")

    _validate_send_payload(payload)

    reply_to = None
    if payload.reply_to_id:
        reply_to = get_reply_target_for_room(payload.reply_to_id, chat_room)
        if not reply_to:
            raise InvalidReplyTargetError(
                "Reply target does not exist or does not belong to this room."
            )

    receiver = get_other_user(chat_room, user)
    media_objects = _get_media_objects(payload.media_ids or [])

    with transaction.atomic():
        message = MessageModel.objects.create(
            chat_room=chat_room,
            couple=chat_room.couple,
            singles=chat_room.singles,
            sender=user,
            receiver=receiver,
            message_type=payload.message_type,
            text=(payload.text or "").strip() or None,
            media_url=payload.media_url,
            sticker_url=payload.sticker_url,
            reply_to=reply_to,
            is_sent=True,
        )

        if media_objects:
            message.medias.set(media_objects)
            
        print("Inside send_message Core: ", message.text)
        updated_room = update_room_after_message_created(
            chat_room=chat_room,
            message=message,
            is_message_sent=True,
            
        )
        print("Updated room last message to:", updated_room.last_message.text)

    return message,updated_room



def edit_message(*, user: UserModel, payload: EditMessageInput) -> MessageModel:
    """
    Edit message text.

    Rules:
    - only sender can edit
    - deleted messages cannot be edited
    - edited text cannot be empty
    - editing does not mutate delivery/read flags
    """
    message = get_room_message_for_user(payload.message_id, user)
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")

    if message.sender_id != user.id:
        raise PermissionDeniedError("Only the sender can edit this message.")

    if message.is_deleted or message.delete_option == DeleteOption.DELETE_FOR_EVERYONE:
        raise InvalidMessagePayloadError("Deleted messages cannot be edited.")

    new_text = (payload.text or "").strip()
    if not new_text:
        raise InvalidMessagePayloadError("Edited message text cannot be empty.")

    message.text = new_text
    message.is_edited = True
    message.save(update_fields=["text", "is_edited"])
    
    updated_room = update_room_after_message_created(
        chat_room=message.chat_room,
        message=message,
    )

    return message, updated_room


def delete_message(*, user: UserModel, payload: DeleteMessageInput) -> MessageModel:
    """
    Delete message for me or for everyone.

    Rules:
    - user must belong to the room
    - delete_for_me creates/updates per-user deletion record
    - delete_for_everyone only allowed for sender
    """
    message = get_room_message_for_user(payload.message_id, user)
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")

    if payload.delete_option == DeleteOption.DELETE_FOR_ME:
        return _delete_message_for_me(user=user, message=message)

    if payload.delete_option == DeleteOption.DELETE_FOR_EVERYONE:
        return _delete_message_for_everyone(user=user, message=message)

    raise InvalidDeleteOperationError("Unsupported delete option.")


def _delete_message_for_me(*, user: UserModel, message: MessageModel) -> MessageModel:
    """
    Hide the message only for the requesting user.
    """
    
    message.delete_option = DeleteOption.DELETE_FOR_ME
    message.save(update_fields=["delete_option"])
    
    updated_room = update_room_after_message_created(
        chat_room=message.chat_room,
        message=message,
    )
    return message, updated_room


def _delete_message_for_everyone(
    *,
    user: UserModel,
    message: MessageModel,
) -> MessageModel:
    """
    Globally delete a message for all room participants.

    Current policy:
    - only sender can perform this action
    """
    if message.sender_id != user.id:
        raise PermissionDeniedError("Only the sender can delete for everyone.")

    deleted_text = "You deleted this message"

    message.is_deleted = True
    message.delete_option = DeleteOption.DELETE_FOR_EVERYONE
    message.deleted_message = deleted_text
    message.text = None
    message.media = None
    message.media_url = None
    message.sticker_url = None
    message.is_edited = False
    message.save(
        update_fields=[
            "is_deleted",
            "delete_option",
            "deleted_message",
            "text",
            "media",
            "media_url",
            "sticker_url",
            "is_edited",
        ]
    )
    
    updated_room = update_room_after_message_created(
        chat_room=message.chat_room,
        message=message,
    )

    return message, updated_room


def _validate_send_payload(payload: SendMessageInput) -> None:
    """
    Validate send-message content rules.
    """
    normalized_text = (payload.text or "").strip()
    media_ids = payload.media_ids or []

    has_text = bool(normalized_text)
    has_media_ids = bool(media_ids)
    has_media_url = bool(payload.media_url)
    has_sticker = bool(payload.sticker_url)

    if not any([has_text, has_media_ids, has_media_url, has_sticker]):
        raise InvalidMessagePayloadError(
            "Message must contain text, media, sticker, or media URL."
        )

    if payload.message_type == MessageType.TEXT and not has_text and not has_media_ids:
        raise InvalidMessagePayloadError(
            "Text messages must include text or media."
        )


def _get_media_objects(media_ids: Iterable[int]) -> list[MediaModel]:
    """
    Fetch media objects in a stable bulk query.
    """
    if not media_ids:
        return []

    return list(MediaModel.objects.filter(id__in=media_ids))