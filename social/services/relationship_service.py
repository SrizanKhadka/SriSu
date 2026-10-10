"""One serialized transition path for invitations and accepted relationships.

Lock active user rows in ID order before invitations, couple, memberships, room.
Terminal invitations are immutable: relinking always creates a new instance.
"""
from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from authentication.models import UserModel
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from social.services.couple_profile_service import create_or_get_couple_for_connection, CoupleProfileConflict
from social.services.moment_service import blocked_user_ids
from utils.choices import CoupleConnectionStatus as Status


class RelationshipConflict(APIException):
    status_code = 409
    default_detail = "The relationship changed. Refresh before trying again."
    default_code = "conflict"


def lock_users(sender_number, receiver_number):
    users = list(UserModel.objects.select_for_update().filter(
        phone_number__in=[sender_number, receiver_number], is_active=True).order_by("id"))
    if len(users) != 2:
        raise ValidationError("Two different active accounts are required.")
    return users


def assert_unblocked(users):
    if users[1].pk in blocked_user_ids(users[0]):
        raise PermissionDenied()


def pair_query(first, second):
    return Q(sender_number=first, receiver_number=second) | Q(sender_number=second, receiver_number=first)


@transaction.atomic
def invite(actor, receiver_number):
    users = lock_users(actor.phone_number, receiver_number)
    assert_unblocked(users)
    if CoupleMembershipModel.objects.filter(user__in=users).exists() or CoupleConnectionModel.objects.filter(
        Q(sender_number__in=[u.phone_number for u in users]) | Q(receiver_number__in=[u.phone_number for u in users]),
        connection_status=Status.ACCEPTED,
    ).exists():
        raise RelationshipConflict()
    existing = CoupleConnectionModel.objects.select_for_update().filter(
        pair_query(actor.phone_number, receiver_number), connection_status=Status.PENDING).order_by("id").first()
    if existing:
        return existing, False
    return CoupleConnectionModel.objects.create(sender_number=actor.phone_number,
        receiver_number=receiver_number, connection_status=Status.PENDING), True


@transaction.atomic
def transition(actor, connection_id, desired, sender_number, receiver_number):
    # Identity comes from the path and stored invitation, never request sender fields.
    snapshot = CoupleConnectionModel.objects.filter(pk=connection_id).first()
    if not snapshot or actor.phone_number not in (snapshot.sender_number, snapshot.receiver_number):
        raise NotFound()
    if (sender_number, receiver_number) != (snapshot.sender_number, snapshot.receiver_number):
        raise ValidationError("Invitation participants do not match.")
    users = lock_users(snapshot.sender_number, snapshot.receiver_number)
    connection = CoupleConnectionModel.objects.select_for_update().get(pk=connection_id)
    if desired not in (Status.ACCEPTED, Status.REJECTED, Status.NOTHING, Status.BREAKUP):
        raise ValidationError("Unsupported transition.")
    recipient = actor.phone_number == connection.receiver_number
    if desired in (Status.ACCEPTED, Status.REJECTED) and not recipient:
        raise PermissionDenied()
    if desired == Status.NOTHING and recipient:
        raise PermissionDenied()
    if desired == Status.ACCEPTED:
        assert_unblocked(users)
        if connection.connection_status not in (Status.PENDING, Status.ACCEPTED):
            raise RelationshipConflict()
        competing = CoupleConnectionModel.objects.filter(
            Q(sender_number__in=[u.phone_number for u in users]) | Q(receiver_number__in=[u.phone_number for u in users]),
            connection_status=Status.ACCEPTED,
        ).exclude(pk=connection.pk)
        if competing.exists():
            raise RelationshipConflict()
        if connection.connection_status != Status.ACCEPTED:
            connection.connection_status = Status.ACCEPTED
            connection.save(update_fields=["connection_status", "updated_at"])
        try:
            couple = create_or_get_couple_for_connection(connection)
            from couple_chat.services import ensure_room
            room = ensure_room(couple)
        except (CoupleProfileConflict, ValueError) as error:
            raise RelationshipConflict() from error
        UserModel.objects.filter(pk__in=[u.pk for u in users]).update(is_engaged=True)
        # Inverse and other pending invitations cannot later establish another couple.
        CoupleConnectionModel.objects.filter(
            Q(sender_number__in=[u.phone_number for u in users]) | Q(receiver_number__in=[u.phone_number for u in users]),
            connection_status=Status.PENDING,
        ).exclude(pk=connection.pk).update(connection_status=Status.NOTHING)
        return connection, couple, room
    if desired == connection.connection_status:
        return connection, None, None
    if desired == Status.BREAKUP:
        if connection.connection_status != Status.ACCEPTED:
            raise RelationshipConflict()
        couple = CoupleModel.objects.select_for_update().filter(couple_connection=connection).first()
        if couple:
            from couple_chat.services import revoke_room
            revoke_room(couple.pk)
            couple.memberships.all().delete()
        UserModel.objects.filter(pk__in=[u.pk for u in users]).update(is_engaged=False)
    elif connection.connection_status != Status.PENDING:
        raise RelationshipConflict()
    connection.connection_status = desired
    connection.save(update_fields=["connection_status", "updated_at"])
    return connection, None, None
