"""Authoritative chat-v2 mutations.

The server stores opaque client envelopes and synchronization metadata.  It does
not encrypt, decrypt, inspect, or claim to authenticate message plaintext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from authentication.models import DeviceSession, UserModel
from chat.models import (
    ChatChange,
    ChatOperation,
    ChatOutbox,
    ChatReceipt,
    ChatRoom,
    EncryptedAttachment,
    MessageModel,
    MessageReaction,
    MessageVisibility,
)
from chat.selectors.access import authorized_rooms
from chat.services.authorization import lock_authorized_room, lock_current_device_session
from utils.choices import DeleteOption, MessageType


class ChatV2Error(Exception):
    code = "validation_failed"


class ChatV2NotFound(ChatV2Error):
    code = "not_found"


class ChatV2Conflict(ChatV2Error):
    code = "conflict"


class ChatV2Unavailable(ChatV2Error):
    code = "encrypted_chat_unavailable"


class ChatV2Validation(ChatV2Error):
    pass


@dataclass(frozen=True)
class MutationResult:
    operation: ChatOperation
    message: MessageModel | None
    change_sequence: int | None
    replayed: bool


@dataclass(frozen=True)
class ReceiptMutationResult:
    operation: ChatOperation
    receipt: ChatReceipt
    change_sequence: int | None
    replayed: bool


def require_encrypted_writes(user: UserModel) -> None:
    protocol_ready = settings.CHAT_V2_PROTOCOL_STATUS == "ready" or (
        settings.CHAT_V2_PROTOCOL_STATUS == "test_adapter"
        and settings.CHAT_V2_TEST_ADAPTER_ENABLED
    )
    if (
        not settings.CHAT_V2_ENCRYPTED_WRITES_ENABLED
        or not protocol_ready
        or user.id not in settings.CHAT_V2_ALLOWED_USER_IDS
    ):
        raise ChatV2Unavailable()


def require_attachment_staging(user: UserModel) -> None:
    require_encrypted_writes(user)
    if not settings.CHAT_V2_ATTACHMENT_STAGING_ENABLED:
        raise ChatV2Unavailable()


def canonical_hash(kind: str, payload: dict) -> str:
    encoded = json.dumps(
        {"kind": kind, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_envelope(envelope) -> dict:
    if not isinstance(envelope, dict) or not envelope:
        raise ChatV2Validation("An opaque encrypted envelope is required.")
    try:
        size = len(json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        raise ChatV2Validation("The encrypted envelope is invalid.") from None
    if size > settings.CHAT_V2_MAX_ENVELOPE_BYTES:
        raise ChatV2Validation("The encrypted envelope is too large.")
    return envelope


def _device_session(user: UserModel, session_id) -> DeviceSession | None:
    if not session_id:
        if settings.CHAT_V2_REQUIRE_DEVICE_SESSION:
            raise ChatV2Validation("A current device session is required.")
        return None
    session = lock_current_device_session(user, session_id)
    if session is None:
        raise ChatV2Validation("A current device session is required.")
    return session


def _lock_room(room_id, user: UserModel) -> ChatRoom:
    room = lock_authorized_room(room_id, user)
    if room is None:
        raise ChatV2NotFound()
    return room


def _other_user(room: ChatRoom, user: UserModel) -> UserModel:
    other = room.user_two if room.user_one_id == user.id else room.user_one
    if other is None:
        raise ChatV2NotFound()
    return other


def _require_room_backfilled(room: ChatRoom) -> None:
    if MessageModel.objects.filter(chat_room=room, sequence__isnull=True).exists():
        raise ChatV2Unavailable()


def _require_room_pilot(room: ChatRoom) -> None:
    participant_ids = {room.user_one_id, room.user_two_id}
    if None in participant_ids or not participant_ids.issubset(
        settings.CHAT_V2_ALLOWED_USER_IDS
    ):
        raise ChatV2Unavailable()


def _begin_operation(
    *,
    room: ChatRoom,
    actor: UserModel,
    operation_id: UUID,
    kind: str,
    payload_hash: str,
) -> tuple[ChatOperation, bool]:
    existing = ChatOperation.objects.select_for_update().filter(
        chat_room=room,
        actor=actor,
        operation_id=operation_id,
    ).first()
    if existing:
        if existing.kind != kind or existing.payload_hash != payload_hash:
            raise ChatV2Conflict("The operation id was already used for a different payload.")
        return existing, True
    return ChatOperation.objects.create(
        chat_room=room,
        actor=actor,
        operation_id=operation_id,
        kind=kind,
        payload_hash=payload_hash,
    ), False


def _allocate_sequence(room: ChatRoom) -> int:
    room.last_sequence += 1
    room.save(update_fields=["last_sequence"])
    return room.last_sequence


def _record_change(
    *,
    room: ChatRoom,
    actor: UserModel,
    kind: str,
    message: MessageModel | None = None,
    audience_user: UserModel | None = None,
    metadata: dict | None = None,
    sequence: int | None = None,
) -> ChatChange:
    sequence = sequence if sequence is not None else _allocate_sequence(room)
    change = ChatChange.objects.create(
        chat_room=room,
        sequence=sequence,
        kind=kind,
        message=message,
        message_revision=message.revision if message else None,
        actor=actor,
        audience_user=audience_user,
        metadata=metadata or {},
    )
    ChatOutbox.objects.create(change=change)
    return change


@transaction.atomic
def record_legacy_change(
    *,
    room_id,
    actor: UserModel,
    kind: str,
    message: MessageModel,
    created: bool = False,
    audience_user: UserModel | None = None,
) -> ChatChange | None:
    """Bridge committed legacy writes into the recoverable v2 change stream."""
    room = ChatRoom.objects.select_for_update().get(pk=room_id)
    unsequenced = MessageModel.objects.filter(chat_room=room, sequence__isnull=True)
    if created:
        unsequenced = unsequenced.exclude(pk=message.pk)
    if unsequenced.exists() or (not created and message.sequence is None):
        # Preserve chronological ordering for the resumable backfill.  V2 writes
        # remain unavailable for this room until the backlog reaches zero.
        return None
    if created and message.sequence is not None:
        existing = ChatChange.objects.filter(
            chat_room=room,
            sequence=message.sequence,
            message=message,
            kind=kind,
        ).first()
        if existing:
            return existing
    sequence = _allocate_sequence(room)
    if created:
        message.sequence = sequence
        message.save(update_fields=["sequence"])
    return _record_change(
        room=room,
        actor=actor,
        kind=kind,
        message=message,
        audience_user=audience_user,
        sequence=sequence,
    )


def _message_for_mutation(room: ChatRoom, user: UserModel, message_id: int) -> MessageModel:
    message = (
        MessageModel.objects.select_for_update()
        .filter(chat_room=room, pk=message_id)
        .exclude(visibility_records__user=user)
        .first()
    )
    if message is None:
        raise ChatV2NotFound()
    return message


def _replayed_message(operation: ChatOperation) -> MessageModel | None:
    message_id = operation.result.get("message_id")
    return MessageModel.objects.filter(pk=message_id).first() if message_id else None


def _normalize_inbound_receipt(room: ChatRoom, user: UserModel, requested: int) -> int:
    """Map a room cursor to the newest visible v2 message received by this user."""
    if requested <= 0:
        return 0
    sequence = (
        MessageModel.objects.filter(
            chat_room=room,
            receiver=user,
            legacy_plaintext=False,
            sequence__isnull=False,
            sequence__lte=requested,
        )
        .exclude(visibility_records__user=user)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    return sequence or 0


@transaction.atomic
def send_encrypted_message(
    *,
    room_id,
    user: UserModel,
    operation_id: UUID,
    content_kind: str,
    envelope: dict,
    reply_to_id: int | None = None,
    attachment_ids: list[UUID] | None = None,
    device_session_id=None,
) -> MutationResult:
    require_encrypted_writes(user)
    if content_kind != "text":
        raise ChatV2Validation("Only encrypted text is enabled for the pilot.")
    envelope = validate_envelope(envelope)
    attachments = list(dict.fromkeys(attachment_ids or []))
    if attachments:
        require_attachment_staging(user)
    payload = {
        "content_kind": content_kind,
        "envelope": envelope,
        "reply_to_id": reply_to_id,
        "attachment_ids": [str(item) for item in attachments],
    }
    digest = canonical_hash("send", payload)
    room = _lock_room(room_id, user)
    _require_room_pilot(room)
    _require_room_backfilled(room)
    operation, replayed = _begin_operation(
        room=room,
        actor=user,
        operation_id=operation_id,
        kind="send",
        payload_hash=digest,
    )
    if replayed:
        return MutationResult(
            operation=operation,
            message=_replayed_message(operation),
            change_sequence=operation.result.get("change_sequence"),
            replayed=True,
        )
    device_session = _device_session(user, device_session_id)
    reply_to = None
    if reply_to_id is not None:
        reply_to = MessageModel.objects.filter(chat_room=room, pk=reply_to_id).first()
        if reply_to is None:
            raise ChatV2NotFound()

    attachment_rows = list(
        EncryptedAttachment.objects.select_for_update().filter(pk__in=attachments).order_by("id")
    )
    if len(attachment_rows) != len(attachments):
        raise ChatV2NotFound()
    now = timezone.now()
    if any(
        item.owner_id != user.id
        or item.chat_room_id != room.id
        or item.state != EncryptedAttachment.State.STAGED
        or item.expires_at <= now
        for item in attachment_rows
    ):
        raise ChatV2NotFound()

    sequence = _allocate_sequence(room)
    if room.encrypted_v2_started_at is None:
        room.encrypted_v2_started_at = timezone.now()
        room.save(update_fields=["encrypted_v2_started_at"])
    receiver = _other_user(room, user)
    message = MessageModel.objects.create(
        chat_room=room,
        couple=room.couple,
        sender=user,
        receiver=receiver,
        message_type=MessageType.TEXT,
        client_operation_id=operation_id,
        payload_hash=digest,
        sequence=sequence,
        content_kind=content_kind,
        encrypted_envelope=envelope,
        legacy_plaintext=False,
        sender_device_session=device_session,
        reply_to=reply_to,
        is_sent=True,
    )
    if attachment_rows:
        EncryptedAttachment.objects.filter(pk__in=attachments).update(
            state=EncryptedAttachment.State.CLAIMED,
            claimed_message=message,
        )
    unread = dict(room.unread_count or {})
    unread[str(receiver.id)] = unread.get(str(receiver.id), 0) + 1
    room.last_message = message
    room.unread_count = unread
    room.save(update_fields=["last_message", "unread_count", "updated_at"])
    change = _record_change(
        room=room,
        actor=user,
        kind="message_created",
        message=message,
        sequence=sequence,
    )
    operation.result = {"message_id": message.pk, "change_sequence": change.sequence}
    operation.save(update_fields=["result"])
    return MutationResult(operation, message, change.sequence, False)


@transaction.atomic
def mutate_message(
    *,
    room_id,
    user: UserModel,
    operation_id: UUID,
    action: str,
    message_id: int,
    expected_revision: int | None = None,
    envelope=None,
    device_session_id=None,
) -> MutationResult:
    require_encrypted_writes(user)
    if action not in {"edit", "delete_for_me", "delete_for_everyone", "set_reaction"}:
        raise ChatV2Validation("Unsupported operation.")
    if action in {"edit", "set_reaction"} and envelope is not None:
        envelope = validate_envelope(envelope)
    payload = {
        "action": action,
        "message_id": message_id,
        "expected_revision": expected_revision,
        "envelope": envelope,
    }
    digest = canonical_hash(action, payload)
    room = _lock_room(room_id, user)
    _require_room_pilot(room)
    _require_room_backfilled(room)
    operation, replayed = _begin_operation(
        room=room,
        actor=user,
        operation_id=operation_id,
        kind=action,
        payload_hash=digest,
    )
    if replayed:
        return MutationResult(
            operation=operation,
            message=_replayed_message(operation),
            change_sequence=operation.result.get("change_sequence"),
            replayed=True,
        )
    _device_session(user, device_session_id)
    message = _message_for_mutation(room, user, message_id)
    if message.legacy_plaintext:
        raise ChatV2Conflict("Legacy plaintext messages cannot be mutated through chat v2.")
    now = timezone.now()

    change = None
    if action == "edit":
        if message.sender_id != user.id or message.tombstoned_at is not None:
            raise ChatV2NotFound()
        if expected_revision is None or message.revision != expected_revision:
            raise ChatV2Conflict("The message changed. Refresh before editing.")
        if now - message.timestamp > timedelta(seconds=settings.CHAT_V2_EDIT_WINDOW_SECONDS):
            raise ChatV2Conflict("The edit window has closed.")
        message.encrypted_envelope = validate_envelope(envelope)
        message.revision += 1
        message.edited_at = now
        message.is_edited = True
        message.save(update_fields=["encrypted_envelope", "revision", "edited_at", "is_edited"])
        change = _record_change(room=room, actor=user, kind="message_edited", message=message)

    elif action == "delete_for_everyone":
        if message.sender_id != user.id or message.tombstoned_at is not None:
            raise ChatV2NotFound()
        if expected_revision is None or message.revision != expected_revision:
            raise ChatV2Conflict("The message changed. Refresh before deleting.")
        if now - message.timestamp > timedelta(seconds=settings.CHAT_V2_DELETE_WINDOW_SECONDS):
            raise ChatV2Conflict("The delete window has closed.")
        message.encrypted_envelope = None
        message.revision += 1
        message.tombstoned_at = now
        message.is_deleted = True
        message.delete_option = DeleteOption.DELETE_FOR_EVERYONE
        message.save(
            update_fields=[
                "encrypted_envelope",
                "revision",
                "tombstoned_at",
                "is_deleted",
                "delete_option",
            ]
        )
        change = _record_change(room=room, actor=user, kind="message_tombstoned", message=message)

    elif action == "delete_for_me":
        _, created = MessageVisibility.objects.get_or_create(message=message, user=user)
        if created:
            change = _record_change(
                room=room,
                actor=user,
                kind="message_hidden",
                message=message,
                audience_user=user,
            )

    else:  # desired-state encrypted reaction; null removes it
        if message.tombstoned_at is not None or message.is_deleted:
            raise ChatV2NotFound()
        reaction = MessageReaction.objects.select_for_update().filter(message=message, user=user).first()
        removed = envelope is None
        if reaction is None:
            if not removed:
                reaction = MessageReaction.objects.create(
                    message=message,
                    user=user,
                    encrypted_envelope=envelope,
                    is_removed=False,
                )
                change = _record_change(room=room, actor=user, kind="reaction_changed", message=message)
        elif reaction.encrypted_envelope != envelope or reaction.is_removed != removed:
            reaction.encrypted_envelope = envelope
            reaction.is_removed = removed
            reaction.revision += 1
            reaction.save(update_fields=["encrypted_envelope", "is_removed", "revision", "updated_at"])
            change = _record_change(room=room, actor=user, kind="reaction_changed", message=message)

    operation.result = {
        "message_id": message.pk,
        "change_sequence": change.sequence if change else None,
    }
    operation.save(update_fields=["result"])
    return MutationResult(operation, message, operation.result["change_sequence"], False)


@transaction.atomic
def advance_receipts(
    *,
    room_id,
    user: UserModel,
    operation_id: UUID,
    delivered_through: int,
    read_through: int,
    device_session_id=None,
) -> ReceiptMutationResult:
    require_encrypted_writes(user)
    if read_through > delivered_through:
        raise ChatV2Validation("Read position cannot exceed delivered position.")
    payload = {"delivered_through": delivered_through, "read_through": read_through}
    digest = canonical_hash("receipt", payload)
    room = _lock_room(room_id, user)
    _require_room_pilot(room)
    _require_room_backfilled(room)
    operation, replayed = _begin_operation(
        room=room,
        actor=user,
        operation_id=operation_id,
        kind="receipt",
        payload_hash=digest,
    )
    receipt, _ = ChatReceipt.objects.select_for_update().get_or_create(chat_room=room, user=user)
    if replayed:
        return ReceiptMutationResult(
            operation,
            receipt,
            operation.result.get("change_sequence"),
            True,
        )
    _device_session(user, device_session_id)
    if delivered_through > room.last_sequence or read_through > room.last_sequence:
        raise ChatV2Validation("Receipt position exceeds the room high-water mark.")
    delivered_through = _normalize_inbound_receipt(room, user, delivered_through)
    read_through = _normalize_inbound_receipt(room, user, read_through)
    next_delivered = max(receipt.delivered_through, delivered_through)
    next_read = max(receipt.read_through, read_through)
    changed = (next_delivered, next_read) != (receipt.delivered_through, receipt.read_through)
    change = None
    if changed:
        receipt.delivered_through = next_delivered
        receipt.read_through = next_read
        receipt.save(update_fields=["delivered_through", "read_through", "updated_at"])
        change = _record_change(
            room=room,
            actor=user,
            kind="receipt_advanced",
            metadata={
                "user_id": user.id,
                "delivered_through": next_delivered,
                "read_through": next_read,
            },
        )
    operation.result = {"change_sequence": change.sequence if change else None}
    operation.save(update_fields=["result"])
    return ReceiptMutationResult(operation, receipt, operation.result["change_sequence"], False)


def visible_changes_window(
    *,
    room: ChatRoom,
    user: UserModel,
    after_sequence: int,
    limit: int,
    max_sequence: int,
):
    """Scan sequence space, then filter private rows so gaps never stall a client."""
    scanned = list(
        ChatChange.objects.filter(
            chat_room=room,
            sequence__gt=after_sequence,
            sequence__lte=max_sequence,
        )
        .select_related("message", "actor", "audience_user")
        .prefetch_related(
            "message__reaction_records",
            "message__encrypted_attachments",
            "message__visibility_records",
        )
        .order_by("sequence")[:limit]
    )
    next_after = scanned[-1].sequence if scanned else min(after_sequence, max_sequence)
    visible = [
        change
        for change in scanned
        if change.audience_user_id is None or change.audience_user_id == user.id
    ]
    has_more = ChatChange.objects.filter(
        chat_room=room,
        sequence__gt=next_after,
        sequence__lte=max_sequence,
    ).exists()
    return visible, has_more, next_after


def message_page(
    *,
    room: ChatRoom,
    user: UserModel,
    before_sequence: int | None,
    limit: int,
    max_sequence: int,
):
    queryset = (
        MessageModel.objects.filter(
            chat_room=room,
            sequence__isnull=False,
            sequence__lte=max_sequence,
        )
        .exclude(delete_option=DeleteOption.DELETE_FOR_ME)
        .exclude(visibility_records__user=user)
        .exclude(
            deletions__user=user,
            deletions__delete_option__in=[
                DeleteOption.DELETE_FOR_ME,
                DeleteOption.CONVERSATION_DELETED,
            ],
        )
        .select_related("sender", "reply_to")
        .prefetch_related("reaction_records", "encrypted_attachments", "visibility_records")
        .order_by("-sequence")
    )
    if before_sequence is not None:
        queryset = queryset.filter(sequence__lt=before_sequence)
    rows = list(queryset[: limit + 1])
    messages = rows[:limit]
    return messages, len(rows) > limit, messages[-1].sequence if len(rows) > limit else None


def stage_encrypted_attachment(
    *,
    room_id,
    user: UserModel,
    operation_id: UUID,
    ciphertext,
    ciphertext_sha256: str,
    device_session_id=None,
) -> tuple[EncryptedAttachment, bool]:
    require_attachment_staging(user)
    if getattr(ciphertext, "content_type", None) != "application/octet-stream":
        raise ChatV2Validation("Encrypted attachments must use application/octet-stream.")
    if ciphertext.size <= 0 or ciphertext.size > settings.CHAT_V2_MAX_ATTACHMENT_BYTES:
        raise ChatV2Validation("Encrypted attachment size is invalid.")
    digest = hashlib.sha256()
    for chunk in ciphertext.chunks():
        digest.update(chunk)
    ciphertext.seek(0)
    if digest.hexdigest() != ciphertext_sha256:
        raise ChatV2Validation("Encrypted attachment digest does not match.")

    stored_name = None
    try:
        with transaction.atomic():
            room = _lock_room(room_id, user)
            _require_room_pilot(room)
            _device_session(user, device_session_id)
            existing = EncryptedAttachment.objects.select_for_update().filter(
                chat_room=room,
                owner=user,
                operation_id=operation_id,
            ).first()
            if existing:
                if (
                    existing.ciphertext_sha256 != ciphertext_sha256
                    or existing.ciphertext_size != ciphertext.size
                ):
                    raise ChatV2Conflict(
                        "The operation id was already used for a different attachment."
                    )
                return existing, True
            attachment = EncryptedAttachment.objects.create(
                chat_room=room,
                owner=user,
                operation_id=operation_id,
                ciphertext=ciphertext,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_size=ciphertext.size,
                expires_at=timezone.now()
                + timedelta(seconds=settings.CHAT_V2_ATTACHMENT_TTL_SECONDS),
            )
            stored_name = attachment.ciphertext.name
            return attachment, False
    except Exception:
        if stored_name:
            EncryptedAttachment._meta.get_field("ciphertext").storage.delete(stored_name)
        raise


def attachment_for_download(*, room_id, attachment_id, user: UserModel) -> EncryptedAttachment:
    room = authorized_rooms(user).filter(pk=room_id).first()
    if room is None:
        raise ChatV2NotFound()
    attachment = (
        EncryptedAttachment.objects.select_related("claimed_message")
        .filter(pk=attachment_id, chat_room=room)
        .first()
    )
    if attachment is None or attachment.state == EncryptedAttachment.State.CANCELLED:
        raise ChatV2NotFound()
    if attachment.state == EncryptedAttachment.State.STAGED:
        if attachment.owner_id != user.id or attachment.expires_at <= timezone.now():
            raise ChatV2NotFound()
        return attachment
    message = attachment.claimed_message
    if message is None or message.tombstoned_at is not None or message.is_deleted:
        raise ChatV2NotFound()
    if MessageVisibility.objects.filter(message=message, user=user).exists():
        raise ChatV2NotFound()
    return attachment


@transaction.atomic
def cancel_encrypted_attachment(
    *,
    room_id,
    attachment_id,
    user: UserModel,
    device_session_id=None,
) -> None:
    require_attachment_staging(user)
    room = _lock_room(room_id, user)
    _require_room_pilot(room)
    _device_session(user, device_session_id)
    attachment = EncryptedAttachment.objects.select_for_update().filter(
        pk=attachment_id,
        chat_room=room,
        owner=user,
    ).first()
    if attachment is None:
        raise ChatV2NotFound()
    if attachment.state == EncryptedAttachment.State.CLAIMED:
        raise ChatV2Conflict("A claimed attachment cannot be cancelled.")
    if attachment.state != EncryptedAttachment.State.CANCELLED:
        attachment.state = EncryptedAttachment.State.CANCELLED
        attachment.save(update_fields=["state", "updated_at"])
