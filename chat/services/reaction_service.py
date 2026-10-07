from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.models import MessageModel, MessageReaction
from chat.services.authorization import lock_authorized_room, require_locked_legacy_session
from chat.websocket.exceptions import (
    InvalidMessagePayloadError,
    MessageNotFoundError,
)
from chat.services.v2 import record_legacy_change


@dataclass(frozen=True)
class ReactionResult:
    message_id: int
    user_id: int
    reaction: str | None
    was_removed: bool


@transaction.atomic
def react_to_message(
    *,
    user: UserModel,
    message_id: int,
    reaction: str,
    device_session_id=None,
) -> tuple[MessageModel, ReactionResult]:
    """
    Toggle or replace a reaction for a message.

    Rules:
    - authenticated user must belong to the message room
    - if user taps same reaction again, remove it
    - if user taps different reaction, replace old one
    """
    if not reaction:
        raise InvalidMessagePayloadError("Reaction is required.")

    room_id = (
        MessageModel.objects.filter(pk=message_id)
        .values_list("chat_room_id", flat=True)
        .first()
    )
    room = lock_authorized_room(room_id, user) if room_id is not None else None
    message = (
        MessageModel.objects.select_for_update(of=("self",))
        .select_related("chat_room", "sender", "receiver")
        .filter(pk=message_id, chat_room=room)
        .first()
        if room is not None
        else None
    )
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")
    require_locked_legacy_session(user, device_session_id)
    if not message.legacy_plaintext:
        raise InvalidMessagePayloadError(
            "Encrypted messages can only be reacted to through chat v2."
        )

    existing = MessageReaction.objects.select_for_update().filter(
        message=message,
        user=user,
    ).first()

    if existing and existing.reaction == reaction:
        existing.delete()
        _sync_legacy_reactions_json(message)
        record_legacy_change(
            room_id=message.chat_room_id,
            actor=user,
            kind="legacy_reaction_changed",
            message=message,
        )
        result = ReactionResult(
            message_id=message.id,
            user_id=user.id,
            reaction=None,
            was_removed=True,
        )
        return message, result

    if existing:
        existing.reaction = reaction
        existing.save(update_fields=["reaction"])
    else:
        MessageReaction.objects.create(
            message=message,
            user=user,
            reaction=reaction,
        )

    _sync_legacy_reactions_json(message)
    record_legacy_change(
        room_id=message.chat_room_id,
        actor=user,
        kind="legacy_reaction_changed",
        message=message,
    )

    result = ReactionResult(
        message_id=message.id,
        user_id=user.id,
        reaction=reaction,
        was_removed=False,
    )
    return message, result


def _sync_legacy_reactions_json(message: MessageModel) -> None:
    """
    Transitional compatibility helper.

    Keeps message.reactions JSON in sync for older clients during migration.
    Remove this once all clients use normalized reaction payloads.
    """
    reactions_map = {
        str(record.user_id): record.reaction
        for record in MessageReaction.objects.filter(message=message)
    }

    message.reactions = reactions_map
    message.save(update_fields=["reactions"])
