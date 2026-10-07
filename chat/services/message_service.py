from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from authentication.models import UserModel
from chat.models import MediaModel, MessageDeletion, MessageModel
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
from chat.services.authorization import lock_authorized_room, require_locked_legacy_session
from chat.services.v2 import record_legacy_change


logger = logging.getLogger(__name__)


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


def send_message(
    *,
    user: UserModel,
    payload: SendMessageInput,
    device_session_id=None,
) -> MessageModel:
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

    _validate_send_payload(payload, chat_room=chat_room)

    with transaction.atomic():
        chat_room = lock_authorized_room(chat_room.pk, user)
        if chat_room is None:
            raise ChatRoomNotFoundError("Chat room not found or access denied.")
        require_locked_legacy_session(user, device_session_id)
        _validate_send_payload(payload, chat_room=chat_room)
        if chat_room.encrypted_v2_started_at is not None:
            raise InvalidMessagePayloadError(
                "This room requires the chat v2 ciphertext protocol."
            )
        reply_to = None
        if payload.reply_to_id:
            reply_to = get_reply_target_for_room(payload.reply_to_id, chat_room)
            if not reply_to:
                raise InvalidReplyTargetError(
                    "Reply target does not exist or does not belong to this room."
                )
        receiver = get_other_user(chat_room, user)
        if receiver is None:
            raise ChatRoomNotFoundError("Chat room not found or access denied.")
        media_objects = _get_media_objects(
            payload.media_ids or [],
            user=user,
            chat_room=chat_room,
        )
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
            MediaModel.objects.filter(pk__in=[media.pk for media in media_objects]).update(
                chat_room=chat_room,
                claimed_at=timezone.now(),
            )

        updated_room = update_room_after_message_created(
            chat_room=chat_room,
            message=message,
            is_message_sent=True,

        )
        record_legacy_change(
            room_id=chat_room.pk,
            actor=user,
            kind="legacy_message_created",
            message=message,
            created=True,
        )

    return message,updated_room



@transaction.atomic
def edit_message(
    *,
    user: UserModel,
    payload: EditMessageInput,
    device_session_id=None,
) -> MessageModel:
    """
    Edit message text.

    Rules:
    - only sender can edit
    - deleted messages cannot be edited
    - edited text cannot be empty
    - editing does not mutate delivery/read flags
    """
    message = _locked_legacy_message_for_user(payload.message_id, user)
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")
    require_locked_legacy_session(user, device_session_id)
    if not message.legacy_plaintext:
        raise InvalidMessagePayloadError(
            "Encrypted messages can only be edited through chat v2."
        )

    if message.sender_id != user.id:
        raise PermissionDeniedError("Only the sender can edit this message.")

    if message.is_deleted or message.delete_option == DeleteOption.DELETE_FOR_EVERYONE:
        raise InvalidMessagePayloadError("Deleted messages cannot be edited.")

    new_text = (payload.text or "").strip()
    if not new_text:
        raise InvalidMessagePayloadError("Edited message text cannot be empty.")

    message.text = new_text
    message.is_edited = True
    message.revision += 1
    message.edited_at = timezone.now()
    message.save(update_fields=["text", "is_edited", "revision", "edited_at"])
    record_legacy_change(
        room_id=message.chat_room_id,
        actor=user,
        kind="legacy_message_edited",
        message=message,
    )
    return message, message.chat_room


@transaction.atomic
def delete_message(
    *,
    user: UserModel,
    payload: DeleteMessageInput,
    device_session_id=None,
) -> MessageModel:
    """
    Delete message for me or for everyone.

    Rules:
    - user must belong to the room
    - delete_for_me creates/updates per-user deletion record
    - delete_for_everyone only allowed for sender
    """
    message = _locked_legacy_message_for_user(payload.message_id, user)
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")
    require_locked_legacy_session(user, device_session_id)
    if not message.legacy_plaintext:
        raise InvalidMessagePayloadError(
            "Encrypted messages can only be deleted through chat v2."
        )

    if payload.delete_option == DeleteOption.DELETE_FOR_ME:
        return _delete_message_for_me(user=user, message=message)

    if payload.delete_option == DeleteOption.DELETE_FOR_EVERYONE:
        return _delete_message_for_everyone(user=user, message=message)

    raise InvalidDeleteOperationError("Unsupported delete option.")


def _delete_message_for_me(*, user: UserModel, message: MessageModel) -> MessageModel:
    """
    Hide the message only for the requesting user.
    """
    
    MessageDeletion.objects.update_or_create(
        message=message,
        user=user,
        delete_option=DeleteOption.DELETE_FOR_ME,
    )
    record_legacy_change(
        room_id=message.chat_room_id,
        actor=user,
        kind="legacy_message_hidden",
        message=message,
        audience_user=user,
    )
    return message, message.chat_room


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
    direct_media_name = message.media.name if message.media else None
    direct_media_storage = message.media.storage if message.media else None

    message.is_deleted = True
    message.delete_option = DeleteOption.DELETE_FOR_EVERYONE
    message.deleted_message = deleted_text
    message.text = None
    message.media_url = None
    message.sticker_url = None
    message.profile_action = None
    message.is_edited = False
    message.tombstoned_at = timezone.now()
    message.revision += 1
    message.save(
        update_fields=[
            "is_deleted",
            "delete_option",
            "deleted_message",
            "text",
            "media_url",
            "sticker_url",
            "profile_action",
            "is_edited",
            "tombstoned_at",
            "revision",
        ]
    )
    message.medias.clear()
    if direct_media_name and direct_media_storage:
        # Storage is not transactional. Keep the inaccessible tombstone's file
        # reference until deletion succeeds, so the bounded cleanup job can retry.
        def delete_direct_media():
            try:
                direct_media_storage.delete(direct_media_name)
            except Exception:
                logger.warning("legacy_direct_media_cleanup_failed")
                return
            MessageModel.objects.filter(
                pk=message.pk,
                media=direct_media_name,
                is_deleted=True,
            ).update(media="")

        transaction.on_commit(delete_direct_media)
    record_legacy_change(
        room_id=message.chat_room_id,
        actor=user,
        kind="legacy_message_tombstoned",
        message=message,
    )
    return message, message.chat_room


def _validate_send_payload(payload: SendMessageInput, *, chat_room) -> None:
    """
    Validate send-message content rules.
    """
    normalized_text = (payload.text or "").strip()
    media_ids = payload.media_ids or []

    has_text = bool(normalized_text)
    has_media_ids = bool(media_ids)
    has_media_url = bool(payload.media_url)
    has_sticker = bool(payload.sticker_url)

    if chat_room.couple_id is not None and (has_media_url or has_sticker):
        raise InvalidMessagePayloadError(
            "Couple chat media must use an owned media upload id."
        )

    if not any([has_text, has_media_ids, has_media_url, has_sticker]):
        raise InvalidMessagePayloadError(
            "Message must contain text, media, sticker, or media URL."
        )

    if payload.message_type == MessageType.TEXT and not has_text and not has_media_ids:
        raise InvalidMessagePayloadError(
            "Text messages must include text or media."
        )


def _locked_legacy_message_for_user(message_id: int, user: UserModel) -> MessageModel | None:
    room_id = (
        MessageModel.objects.filter(pk=message_id)
        .values_list("chat_room_id", flat=True)
        .first()
    )
    if room_id is None:
        return None
    room = lock_authorized_room(room_id, user)
    if room is None:
        return None
    return (
        MessageModel.objects.select_for_update(of=("self",))
        .select_related(
            "chat_room",
            "sender",
            "receiver",
            "reply_to",
            "reply_to__sender",
        )
        .prefetch_related("medias")
        .filter(pk=message_id, chat_room=room)
        .first()
    )


def _get_media_objects(
    media_ids: Iterable[int],
    *,
    user: UserModel,
    chat_room: ChatRoom,
) -> list[MediaModel]:
    """
    Fetch media objects in a stable bulk query.
    """
    if not media_ids:
        return []

    requested = list(dict.fromkeys(media_ids))
    media = list(MediaModel.objects.select_for_update().filter(id__in=requested).order_by("id"))
    if len(media) != len(requested):
        raise InvalidMessagePayloadError("One or more media uploads are unavailable.")
    if any(item.owner_id != user.id for item in media):
        raise PermissionDeniedError("One or more media uploads are unavailable.")
    if any(item.chat_room_id not in (None, chat_room.id) for item in media):
        raise PermissionDeniedError("One or more media uploads are unavailable.")
    return media
