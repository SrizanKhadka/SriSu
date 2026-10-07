"""Transactional authorization shared by chat writers.

Lock order is always the two participant user rows (ascending id), then the
room row. Relationship accept/end operations lock the same user rows first, so
membership revocation and a chat write cannot both commit in the wrong order.
"""

from authentication.models import UserModel
from chat.models import ChatRoom
from chat.selectors.access import authorized_rooms
from chat.websocket.exceptions import PermissionDeniedError


def lock_authorized_room(room_id, user: UserModel) -> ChatRoom | None:
    """Lock a room's principals, then revalidate current server-side access."""
    snapshot = (
        ChatRoom.objects.filter(pk=room_id)
        .values("user_one_id", "user_two_id")
        .first()
    )
    if snapshot is None:
        return None
    participant_ids = sorted(
        item
        for item in (snapshot["user_one_id"], snapshot["user_two_id"])
        if item is not None
    )
    if user.pk not in participant_ids or len(participant_ids) != 2:
        return None

    locked_user_ids = list(
        UserModel.objects.select_for_update()
        .filter(pk__in=participant_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if locked_user_ids != participant_ids:
        return None

    room = (
        ChatRoom.objects.select_for_update(of=("self",))
        .select_related("user_one", "user_two", "couple", "singles")
        .filter(pk=room_id)
        .first()
    )
    current_participant_ids = sorted(
        item for item in (room.user_one_id, room.user_two_id) if item is not None
    ) if room is not None else []
    if room is None or current_participant_ids != participant_ids:
        return None
    if not authorized_rooms(user).filter(pk=room.pk).exists():
        return None
    return room


def lock_current_device_session(user: UserModel, session_id):
    """Lock and return a current session, or None for missing/invalid ids."""
    if not session_id:
        return None
    from authentication.models import DeviceSession
    from django.utils import timezone

    return (
        DeviceSession.objects.select_for_update()
        .filter(
            pk=session_id,
            user=user,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )


def require_locked_legacy_session(user: UserModel, session_id) -> None:
    """Sid-less compatibility is handled by auth policy; supplied sids must lock."""
    if session_id and lock_current_device_session(user, session_id) is None:
        raise PermissionDeniedError("The device session is no longer current.")
