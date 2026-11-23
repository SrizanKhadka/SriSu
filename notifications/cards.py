"""Correlate feature cards before Matrix ingress, without inspecting ciphertext."""
from uuid import UUID
from django.db import transaction
from rest_framework.exceptions import NotFound
from chat.models import MatrixRoomMapping
from chat.selectors.access import authorized_rooms
from .models import CardBinding

MARKER = "org.srisu.notification_card"


@transaction.atomic
def reserve(user, kind, resource):
    if kind == "plan":
        from social.services.plans import plan_for
        obj, _ = plan_for(user, resource, lock=True)
        available = not obj.cancelled_at and not obj.completed and obj.response not in ("yes", "no")
        creator = obj.created_by_id
    elif kind == "challenge":
        from social.services.challenges import challenge_for
        obj = challenge_for(user, resource, lock=True)
        available = obj.state == "invited"
        creator = obj.creator_id
    elif kind == "spark":
        from social.services.sparks import session_for
        obj, _ = session_for(user, resource, lock=True)
        available = obj.state != "skipped"
        creator = obj.creator_id
    else:
        raise NotFound()
    if not available or creator != user.pk:
        raise NotFound()
    room = authorized_rooms(user).filter(couple_id=obj.couple_id).first()
    mapping = MatrixRoomMapping.objects.filter(chat_room=room, state="active", replacement_pending=False,
        pending_remote_revocations=[]).first() if room else None
    if mapping is None:
        raise NotFound()
    binding, _ = CardBinding.objects.get_or_create(actor=user, room=room, epoch=mapping.membership_epoch,
        kind=kind, resource=str(obj.pk))
    return binding


def match_marker(value, sender, mapping, event_id):
    """Call under the AS mapping lock. A key can identify only one Matrix event."""
    try:
        key = UUID(value) if isinstance(value, str) and len(value) == 36 else None
    except ValueError:
        return None
    if key is None:
        return None
    binding = CardBinding.objects.select_for_update().filter(pk=key, actor=sender,
        room_id=mapping.chat_room_id, epoch=mapping.membership_epoch).first()
    if binding is None or (binding.event_id and binding.event_id != event_id):
        return None
    if not binding.event_id:
        binding.event_id = event_id
        binding.save(update_fields=["event_id"])
    return binding.kind
