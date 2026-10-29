"""Current relationship authorization shared by HTTP, commands and delivery."""
from django.db.models import Exists, OuterRef, Q

from chat.models import ChatRoom
from social.models import CoupleMembershipModel, CoupleModel, SingleConnectionModel
from utils.choices import CoupleConnectionStatus, SingleConnectionStatus


def eligible_relationship_rooms():
    """Rooms whose two current partners may still use private messaging.

    A legacy ``SingleConnectionModel.BLOCKED`` edge remains an authoritative
    privacy control even though the old singles/chat surfaces were retired.
    Match it in either direction and keep it in the server-side selector so
    HTTP authorization and the background reconciler cannot disagree.
    """
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
    blocked = SingleConnectionModel.objects.filter(
        connection_status=SingleConnectionStatus.BLOCKED,
    ).filter(
        Q(
            sender_number=OuterRef("user_one__phone_number"),
            receiver_number=OuterRef("user_two__phone_number"),
        )
        | Q(
            sender_number=OuterRef("user_two__phone_number"),
            receiver_number=OuterRef("user_one__phone_number"),
        )
    )
    return ChatRoom.objects.alias(
        first_member=Exists(first),
        second_member=Exists(second),
        blocked_pair=Exists(blocked),
    ).filter(
        user_one__is_active=True,
        user_two__is_active=True,
        couple__isnull=False,
        blocked_pair=False,
    ).filter(
        Q(couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
          couple__couple_connection__ended_at__isnull=True,
          first_member=True, second_member=True)
    )


def eligible_relationship_members() -> dict[int, tuple[int, int]]:
    """Authoritative eligible relationships, including ones missing a room."""
    couples = list(
        CoupleModel.objects.filter(
            couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
            couple_connection__ended_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    memberships = (
        CoupleMembershipModel.objects.filter(couple_id__in=couples)
        .select_related("user")
        .order_by("couple_id", "position")
    )
    grouped = {}
    for membership in memberships:
        grouped.setdefault(membership.couple_id, []).append(membership.user)
    blocked_pairs = {
        frozenset((sender, receiver))
        for sender, receiver in SingleConnectionModel.objects.filter(
            connection_status=SingleConnectionStatus.BLOCKED,
        ).values_list("sender_number", "receiver_number")
    }
    eligible = {}
    for couple_id in couples:
        users = grouped.get(couple_id, [])
        if (
            len(users) == 2
            and all(user.is_active for user in users)
            and frozenset(user.phone_number for user in users) not in blocked_pairs
        ):
            eligible[couple_id] = tuple(sorted(user.pk for user in users))
    return eligible


def authorized_rooms(user):
    if not user or not user.is_authenticated or not user.is_active:
        return ChatRoom.objects.none()
    return eligible_relationship_rooms().filter(
        Q(user_one_id=user.pk) | Q(user_two_id=user.pk)
    )
