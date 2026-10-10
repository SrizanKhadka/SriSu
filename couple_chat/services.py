"""Relationship-scoped room authorization. Never imports legacy chat business logic."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound

from social.models import CoupleMembershipModel, CoupleModel
from social.services.moment_service import active_couples, blocked_user_ids
from .models import Change, Room

logger = logging.getLogger("srisu.couple_chat")


def available_rooms(user):
    if not user.is_active:
        return Room.objects.none()
    # Membership row IDs stop a removed/re-added account inheriting the old room.
    current = CoupleMembershipModel.objects.filter(user=user).values("id")
    blocked = list(blocked_user_ids(user))
    return _available_rooms(user, current, blocked)


def _available_rooms(user, current, blocked):
    from django.db.models import Exists, OuterRef
    memberships = CoupleMembershipModel.objects
    return Room.objects.filter(
        Q(first=user, first_membership_id__in=current) | Q(second=user, second_membership_id__in=current),
        revoked_at__isnull=True, couple_id__in=active_couples().values("id"),
        first__is_active=True, second__is_active=True,
    ).exclude(first_id__in=blocked).exclude(second_id__in=blocked).filter(
        Exists(memberships.filter(pk=OuterRef("first_membership_id"), user_id=OuterRef("first_id"), couple_id=OuterRef("couple_id"))),
        Exists(memberships.filter(pk=OuterRef("second_membership_id"), user_id=OuterRef("second_id"), couple_id=OuterRef("couple_id"))),
    )


def room_for(user, room_id):
    room = available_rooms(user).filter(pk=room_id).first()
    if room is None:
        raise NotFound()
    return room


def describe(room):
    from .protocol import live_devices
    ready=live_devices().filter(user_id__in=[room.first_id,room.second_id]).count()==2
    return {"id": str(room.id), "relationship_id": room.couple_id,
            "participant_ids": [room.first_id, room.second_id], "sequence": room.sequence,
            "participant_names": {str(room.first_id): room.first.full_name, str(room.second_id): room.second.full_name},
            "security_state": "ready" if ready else "setup_required", "can_send": ready}


def append_change(room, kind, metadata=None):
    # Caller holds the room or relationship lock inside an atomic transaction.
    room.sequence += 1
    room.save(update_fields=["sequence"])
    change = Change.objects.create(room=room, sequence=room.sequence, kind=kind, metadata=metadata or {})
    transaction.on_commit(lambda: dispatch(change.pk))
    return change


@transaction.atomic
def ensure_room(couple):
    couple = CoupleModel.objects.select_for_update().get(pk=couple.pk)
    members = list(couple.memberships.select_related("user").order_by("user_id"))
    if len(members) != 2 or not active_couples().filter(pk=couple.pk).exists():
        raise ValueError("An accepted relationship with two current members is required.")
    room, created = Room.objects.get_or_create(couple=couple, defaults={
        "first_id": members[0].user_id, "second_id": members[1].user_id,
        "first_membership_id": members[0].pk, "second_membership_id": members[1].pk,
    })
    if (room.revoked_at or room.first_membership_id != members[0].pk or room.second_membership_id != members[1].pk):
        raise ValueError("A new relationship instance is required.")
    if created:
        append_change(room, "room.created")
    return room


def revoke_room(couple_id):
    room = Room.objects.select_for_update().filter(couple_id=couple_id).first()
    if room and room.revoked_at is None:
        room.revoked_at = timezone.now()
        room.save(update_fields=["revoked_at"])
        append_change(room, "room.revoked")


def notification_hook(change_id):
    """Reserved no-op. Future notifications require an explicit privacy policy."""


def dispatch(change_id):
    change = Change.objects.select_related("room").filter(pk=change_id, dispatched_at__isnull=True).first()
    if change is None:
        return True
    try:
        layer = get_channel_layer()
        for user_id in (change.room.first_id, change.room.second_id):
            # No room identifier/content: even a stale queued event is only a hint
            # to re-fetch an authenticated, current relationship snapshot.
            async_to_sync(layer.group_send)(f"couple_chat.user.{user_id}", {"type": "state.changed"})
        notification_hook(change.pk)
    except Exception:
        # Do not log exception text: backend addresses/credentials may be present.
        logger.warning("couple_chat_dispatch_failed")
        return False
    Change.objects.filter(pk=change.pk, dispatched_at__isnull=True).update(dispatched_at=timezone.now())
    return True
