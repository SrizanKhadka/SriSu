"""Transactional relationship lifecycle and relationship-scoped chat identity."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from authentication.models import UserModel
from chat.models import ChatRoom
from chat.websocket.events import ChatSocketEvents
from chat.websocket.responses import socket_event
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from social.services.couple_profile_service import (
    CoupleProfileConflict,
    create_or_get_couple_for_connection,
)
from utils.choices import ChatTypeChoices, CoupleConnectionStatus


class RelationshipConflict(ValueError):
    pass


class RelationshipPermissionDenied(ValueError):
    pass


logger = logging.getLogger(__name__)


def _publish_relationship_changed(
    *,
    connection: CoupleConnectionModel,
    room: ChatRoom,
    user_ids: list[int],
) -> None:
    """Best-effort, content-free wake-up hint; HTTP remains authoritative."""
    wire_status = (
        "ENDED"
        if connection.connection_status == CoupleConnectionStatus.BREAKUP
        else connection.connection_status
    )
    payload = socket_event(
        action=ChatSocketEvents.RELATIONSHIP_CHANGED,
        data={
            "connection_id": connection.pk,
            "chat_room_id": str(room.pk),
            "revision": connection.revision,
            "status": wire_status,
        },
    )
    event = {
        "type": "chat.broadcast",
        "room_id": str(room.pk),
        "payload": payload,
    }
    try:
        layer = get_channel_layer()
        for user_id in user_ids:
            async_to_sync(layer.group_send)(f"chat_user_{user_id}", event)
    except Exception:
        logger.warning("relationship_publication_unavailable")


def _notify_relationship_changed_on_commit(
    *,
    connection: CoupleConnectionModel,
    room: ChatRoom,
    user_ids: list[int],
) -> None:
    connection_snapshot = CoupleConnectionModel(
        id=connection.pk,
        revision=connection.revision,
        connection_status=connection.connection_status,
    )
    transaction.on_commit(
        lambda: _publish_relationship_changed(
            connection=connection_snapshot,
            room=room,
            user_ids=user_ids,
        )
    )


@dataclass(frozen=True)
class AcceptedRelationship:
    connection: CoupleConnectionModel
    couple: CoupleModel
    chat_room: ChatRoom
    replayed: bool


@dataclass(frozen=True)
class RequestedRelationship:
    connection: CoupleConnectionModel
    replayed: bool


def _participants(
    connection: CoupleConnectionModel,
    *,
    lock: bool,
    require_active: bool = True,
) -> list[UserModel]:
    queryset = UserModel.objects.filter(
        phone_number__in=[connection.sender_number, connection.receiver_number],
    ).order_by("id")
    if require_active:
        queryset = queryset.filter(is_active=True)
    if lock:
        queryset = queryset.select_for_update()
    users = list(queryset)
    if len(users) != 2 or users[0].id == users[1].id:
        raise RelationshipConflict("Both active users are required for this relationship.")
    return users


def ensure_relationship_chat_room(couple: CoupleModel) -> ChatRoom:
    """Return the only room for this relationship without consulting pair history."""
    members = list(
        couple.memberships.filter(ended_at__isnull=True)
        .select_related("user")
        .order_by("position", "id")
    )
    if len(members) != 2:
        raise RelationshipConflict("An active relationship must contain two members.")
    users = sorted((members[0].user, members[1].user), key=lambda item: item.id)
    room, _ = ChatRoom.objects.get_or_create(
        couple=couple,
        defaults={
            "user_one": users[0],
            "user_two": users[1],
            "chat_type": ChatTypeChoices.COUPLE,
        },
    )
    updates = []
    for name, value in (("user_one", users[0]), ("user_two", users[1])):
        if getattr(room, f"{name}_id") != value.id:
            setattr(room, name, value)
            updates.append(name)
    if room.chat_type != ChatTypeChoices.COUPLE:
        room.chat_type = ChatTypeChoices.COUPLE
        updates.append("chat_type")
    if updates:
        room.save(update_fields=[*updates, "updated_at"])
    return room


@transaction.atomic
def create_connection_request(
    *,
    sender_number: str,
    receiver_number: str,
    actor: UserModel,
    operation_id: UUID | None = None,
) -> RequestedRelationship:
    """Create at most one current invitation for a pair under ordered locks."""
    if actor.phone_number != sender_number:
        raise RelationshipPermissionDenied("Only the sender can create this request.")
    users = list(
        UserModel.objects.select_for_update()
        .filter(phone_number__in=[sender_number, receiver_number], is_active=True)
        .order_by("id")
    )
    if len(users) != 2 or users[0].id == users[1].id:
        raise RelationshipConflict("Both active users are required for this request.")

    if operation_id is not None:
        replay = CoupleConnectionModel.objects.select_for_update().filter(
            sender_number=sender_number,
            request_operation_id=operation_id,
        ).first()
        if replay is not None:
            if replay.receiver_number != receiver_number:
                raise RelationshipConflict(
                    "The idempotency key was already used for another request."
                )
            return RequestedRelationship(connection=replay, replayed=True)

    if CoupleMembershipModel.objects.select_for_update().filter(
        user_id__in=[user.id for user in users],
        ended_at__isnull=True,
    ).exists():
        raise RelationshipConflict("One of the users already belongs to an active couple.")

    current = (
        CoupleConnectionModel.objects.select_for_update()
        .filter(
            Q(sender_number=sender_number, receiver_number=receiver_number)
            | Q(sender_number=receiver_number, receiver_number=sender_number),
            connection_status=CoupleConnectionStatus.PENDING,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if current is not None:
        if current.sender_number != sender_number:
            raise RelationshipConflict(
                "The other user already sent a pending request."
            )
        if operation_id is not None and current.request_operation_id is None:
            current.request_operation_id = operation_id
            current.save(update_fields=["request_operation_id", "updated_at"])
        return RequestedRelationship(connection=current, replayed=True)

    return RequestedRelationship(
        connection=CoupleConnectionModel.objects.create(
            sender_number=sender_number,
            receiver_number=receiver_number,
            connection_status=CoupleConnectionStatus.PENDING,
            request_operation_id=operation_id,
        ),
        replayed=False,
    )


@transaction.atomic
def accept_connection(
    *,
    connection_id: int,
    actor: UserModel,
    operation_id: UUID | None = None,
) -> AcceptedRelationship:
    """Accept once under locks, then repair/replay the same committed result."""
    snapshot = CoupleConnectionModel.objects.filter(pk=connection_id).first()
    if snapshot is None:
        raise RelationshipConflict("Connection does not exist.")
    if actor.phone_number != snapshot.receiver_number:
        raise RelationshipPermissionDenied("Only the receiving partner can accept this request.")
    users = _participants(snapshot, lock=True)
    connection = CoupleConnectionModel.objects.select_for_update().get(pk=connection_id)
    if {
        connection.sender_number,
        connection.receiver_number,
    } != {snapshot.sender_number, snapshot.receiver_number}:
        raise RelationshipConflict("The request participants changed.")
    replayed = connection.connection_status == CoupleConnectionStatus.ACCEPTED
    if not replayed and connection.connection_status != CoupleConnectionStatus.PENDING:
        raise RelationshipConflict("Only a pending request can be accepted.")

    active_memberships = list(
        CoupleMembershipModel.objects.select_for_update()
        .filter(user_id__in=[user.id for user in users], ended_at__isnull=True)
        .select_related("couple")
    )
    existing_couple_ids = {membership.couple_id for membership in active_memberships}
    existing_for_connection = CoupleModel.objects.filter(couple_connection=connection).first()
    if existing_couple_ids and (
        existing_for_connection is None or existing_couple_ids != {existing_for_connection.id}
    ):
        raise RelationshipConflict("One of the users already belongs to another active couple.")

    if not replayed:
        now = timezone.now()
        connection.connection_status = CoupleConnectionStatus.ACCEPTED
        connection.accepted_at = now
        connection.ended_at = None
        connection.revision += 1
        if operation_id is not None:
            connection.acceptance_operation_id = operation_id
        connection.save(
            update_fields=[
                "connection_status",
                "acceptance_operation_id",
                "accepted_at",
                "ended_at",
                "revision",
                "updated_at",
            ]
        )
        numbers = [user.phone_number for user in users]
        CoupleConnectionModel.objects.select_for_update().filter(
            Q(sender_number__in=numbers) | Q(receiver_number__in=numbers),
            connection_status=CoupleConnectionStatus.PENDING,
        ).exclude(pk=connection.pk).update(
            connection_status=CoupleConnectionStatus.NOTHING,
            revision=F("revision") + 1,
            updated_at=now,
        )

    try:
        couple = create_or_get_couple_for_connection(connection)
    except CoupleProfileConflict as exc:
        raise RelationshipConflict(str(exc)) from exc
    room = ensure_relationship_chat_room(couple)
    UserModel.objects.filter(pk__in=[user.pk for user in users]).update(is_engaged=True)
    _notify_relationship_changed_on_commit(
        connection=connection,
        room=room,
        user_ids=[user.pk for user in users],
    )
    return AcceptedRelationship(connection=connection, couple=couple, chat_room=room, replayed=replayed)


@transaction.atomic
def end_connection(*, connection_id: int, actor: UserModel) -> CoupleConnectionModel:
    """End current membership without deleting either relationship or chat history."""
    snapshot = CoupleConnectionModel.objects.filter(pk=connection_id).first()
    if snapshot is None:
        raise RelationshipConflict("Connection does not exist.")
    if actor.phone_number not in {snapshot.sender_number, snapshot.receiver_number}:
        raise RelationshipPermissionDenied("Only a relationship member can end it.")
    users = _participants(snapshot, lock=True, require_active=False)
    connection = CoupleConnectionModel.objects.select_for_update().get(pk=connection_id)
    if (
        connection.sender_number != snapshot.sender_number
        or connection.receiver_number != snapshot.receiver_number
    ):
        raise RelationshipConflict("The relationship participants changed.")
    if actor.id not in {user.id for user in users}:
        raise RelationshipPermissionDenied("Only a relationship member can end it.")
    if connection.connection_status != CoupleConnectionStatus.ACCEPTED:
        raise RelationshipConflict("Only an accepted relationship can be ended.")
    ended_at = timezone.now()
    couple = CoupleModel.objects.select_for_update().filter(couple_connection=connection).first()
    room = (
        ChatRoom.objects.select_for_update().filter(couple=couple).first()
        if couple
        else None
    )
    if couple:
        CoupleMembershipModel.objects.select_for_update().filter(
            couple=couple,
            ended_at__isnull=True,
        ).update(ended_at=ended_at)
    connection.connection_status = CoupleConnectionStatus.BREAKUP
    connection.ended_at = ended_at
    connection.revision += 1
    connection.save(update_fields=["connection_status", "ended_at", "revision", "updated_at"])
    UserModel.objects.filter(pk__in=[user.pk for user in users]).update(is_engaged=False)
    if room is not None:
        _notify_relationship_changed_on_commit(
            connection=connection,
            room=room,
            user_ids=[user.pk for user in users],
        )
    return connection


@transaction.atomic
def reconcile_accepted_relationship(couple_id: int) -> ChatRoom:
    couple = CoupleModel.objects.select_for_update().select_related("couple_connection").get(pk=couple_id)
    if couple.couple_connection.connection_status != CoupleConnectionStatus.ACCEPTED:
        raise RelationshipConflict("Only accepted relationships can have an active room.")
    return ensure_relationship_chat_room(couple)
