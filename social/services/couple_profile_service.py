from django.db import transaction

from authentication.models import UserModel
from social.models import CoupleMembershipModel, CoupleModel
from utils.choices import CoupleConnectionStatus


class CoupleProfileConflict(ValueError):
    pass


@transaction.atomic
def create_or_get_couple_for_connection(connection):
    """Create the shared relationship record and its two exclusive memberships."""
    if connection.connection_status != CoupleConnectionStatus.ACCEPTED:
        raise CoupleProfileConflict("The couple connection must be accepted first.")

    users = list(
        UserModel.objects.select_for_update()
        .filter(
            phone_number__in=[connection.sender_number, connection.receiver_number],
            is_active=True,
        )
        .order_by("id")
    )
    if len(users) != 2:
        raise CoupleProfileConflict("Both active users are required for a couple profile.")

    existing_memberships = {
        membership.user_id: membership
        for membership in CoupleMembershipModel.objects.select_related("couple").filter(
            user_id__in=[user.id for user in users]
        )
    }
    existing_couple_ids = {
        membership.couple_id for membership in existing_memberships.values()
    }
    if len(existing_couple_ids) > 1:
        raise CoupleProfileConflict("One of the users already belongs to another couple.")

    couple, _ = CoupleModel.objects.get_or_create(couple_connection=connection)
    if existing_couple_ids and couple.id not in existing_couple_ids:
        raise CoupleProfileConflict("One of the users already belongs to another couple.")

    expected_user_ids = {user.id for user in users}
    if couple.memberships.exclude(user_id__in=expected_user_ids).exists():
        raise CoupleProfileConflict(
            "The existing couple membership does not match this connection."
        )

    for position, user in enumerate(users, start=1):
        CoupleMembershipModel.objects.get_or_create(
            user=user,
            defaults={
                "couple": couple,
                "position": position,
                "is_owner": True,
            },
        )

    if couple.memberships.count() != 2:
        raise CoupleProfileConflict("A couple profile must contain exactly two members.")

    return couple
