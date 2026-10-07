"""Current relationship authorization shared by HTTP, commands and delivery."""
from django.db.models import Exists, OuterRef, Q

from chat.models import ChatRoom
from social.models import CoupleMembershipModel
from utils.choices import CoupleConnectionStatus


def authorized_rooms(user):
    if not user or not user.is_authenticated or not user.is_active:
        return ChatRoom.objects.none()
    first = CoupleMembershipModel.objects.filter(
        couple_id=OuterRef("couple_id"),
        user_id=OuterRef("user_one_id"),
        ended_at__isnull=True,
    )
    second = CoupleMembershipModel.objects.filter(
        couple_id=OuterRef("couple_id"),
        user_id=OuterRef("user_two_id"),
        ended_at__isnull=True,
    )
    return ChatRoom.objects.alias(first_member=Exists(first), second_member=Exists(second)).filter(
        Q(user_one_id=user.pk) | Q(user_two_id=user.pk),
        user_one__is_active=True, user_two__is_active=True,
    ).filter(couple__isnull=False).filter(
        Q(couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
          couple__couple_connection__ended_at__isnull=True,
          first_member=True, second_member=True)
    )


def relationship_hint_for_user(user, *, room_id, connection_id):
    """Return an authoritative participant-only lifecycle hint projection."""
    if not user or not user.is_authenticated or not user.is_active:
        return None
    room = (
        ChatRoom.objects.select_related("couple__couple_connection")
        .filter(pk=room_id, couple__couple_connection_id=connection_id)
        .first()
    )
    if room is None:
        return None
    connection = room.couple.couple_connection
    if user.phone_number not in {
        connection.sender_number,
        connection.receiver_number,
    }:
        return None
    if connection.connection_status == CoupleConnectionStatus.ACCEPTED:
        wire_status = "ACCEPTED"
    elif connection.connection_status == CoupleConnectionStatus.BREAKUP:
        wire_status = "ENDED"
    else:
        return None
    return {
        "connection_id": connection.pk,
        "chat_room_id": str(room.pk),
        "revision": connection.revision,
        "status": wire_status,
    }
