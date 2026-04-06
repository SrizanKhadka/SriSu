from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from authentication.models import UserModel
from chat.models import MessageModel, MessageReaction
from chat.selectors.message_selectors import get_room_message_for_user
from chat.websocket.exceptions import (
    InvalidMessagePayloadError,
    MessageNotFoundError,
)


@dataclass(frozen=True)
class ReactionResult:
    message_id: int
    user_id: int
    reaction: str | None
    was_removed: bool


def react_to_message(
    *,
    user: UserModel,
    message_id: int,
    reaction: str,
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

    message = get_room_message_for_user(message_id, user)
    if not message:
        raise MessageNotFoundError("Message not found or access denied.")

    with transaction.atomic():
        existing = MessageReaction.objects.filter(
            message=message,
            user=user,
        ).first()

        if existing and existing.reaction == reaction:
            existing.delete()
            _sync_legacy_reactions_json(message)
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