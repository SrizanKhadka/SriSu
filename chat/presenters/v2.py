from __future__ import annotations

from chat.models import EncryptedAttachment, MessageModel


def _related_rows(instance, name):
    manager = getattr(instance, name)
    cache = getattr(instance, "_prefetched_objects_cache", {})
    if name in cache:
        return list(cache[name])
    return list(manager.all())


def serialize_message_v2(message: MessageModel, user) -> dict:
    visibility = _related_rows(message, "visibility_records")
    hidden = any(record.user_id == user.id for record in visibility)
    tombstone = message.tombstoned_at is not None or message.is_deleted
    expose_envelope = not hidden and not tombstone and not message.legacy_plaintext
    reactions = []
    if expose_envelope:
        reactions = [
            {
                "user_id": reaction.user_id,
                "revision": reaction.revision,
                "envelope": reaction.encrypted_envelope,
                "is_removed": reaction.is_removed,
            }
            for reaction in _related_rows(message, "reaction_records")
        ]
    attachment_ids = []
    if expose_envelope:
        attachment_ids = [
            str(attachment.pk)
            for attachment in _related_rows(message, "encrypted_attachments")
            if attachment.state == EncryptedAttachment.State.CLAIMED
        ]
    return {
        "id": message.pk,
        "operation_id": (
            str(message.client_operation_id)
            if message.client_operation_id is not None
            else None
        ),
        "sequence": message.sequence,
        "revision": message.revision,
        "sender_id": message.sender_id,
        "content_kind": message.content_kind,
        "envelope": message.encrypted_envelope if expose_envelope else None,
        "reply_to_id": message.reply_to_id,
        "attachment_ids": attachment_ids,
        "reactions": reactions,
        "is_tombstone": tombstone,
        "hidden_for_viewer": hidden,
        "legacy_plaintext": message.legacy_plaintext,
        "created_at": message.timestamp.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
    }


def serialize_change_v2(change, user) -> dict:
    return {
        "sequence": change.sequence,
        "kind": change.kind,
        "message_id": change.message_id,
        "message_revision": change.message_revision,
        "actor_id": change.actor_id,
        "created_at": change.created_at.isoformat(),
        "message": serialize_message_v2(change.message, user) if change.message else None,
        "payload": change.metadata,
    }
