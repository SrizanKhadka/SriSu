"""Personal labels, scoped to the setter's current relationship membership."""
import unicodedata
from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, ValidationError
from authentication.models import UserModel
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from utils.choices import CoupleConnectionStatus


class NicknameConflict(APIException):
    status_code = 409
    default_code = "partner_nickname_changed"
    default_detail = "Your partner or nickname changed. Refresh and try again."


def normalize_nickname(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError({"nickname": "Enter a nickname."})
    # Keep emoji joiners and every script. Reject controls/bidi overrides before
    # normalizing whitespace, so invisible direction changes cannot impersonate UI.
    if any(unicodedata.category(c) in {"Cc", "Cs"} or
           (unicodedata.category(c) == "Cf" and c not in "\u200c\u200d") for c in value):
        raise ValidationError({"nickname": "Use a single-line nickname without control characters."})
    value = " ".join(unicodedata.normalize("NFC", value).split())
    if value and not any(unicodedata.category(c)[0] not in {"M", "C", "Z"} for c in value):
        raise ValidationError({"nickname": "Enter a visible nickname."})
    if len(value) > 40:
        raise ValidationError({"nickname": "Use 40 characters or fewer."})
    return value


def _valid_members(connection, members, user):
    return (len(members) == 2 and all(m.user.is_active for m in members)
        and {m.user.phone_number for m in members} == {connection.sender_number, connection.receiver_number}
        and user.pk in {m.user_id for m in members})


def aliases_for(user):
    """Return only the viewer's current alias. Never cache this on a long-lived User."""
    own = (CoupleMembershipModel.objects.select_related("couple__couple_connection")
        .filter(user=user, couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED)
        .first())
    if own is None or not own.partner_nickname:
        return {}
    members = list(CoupleMembershipModel.objects.filter(couple_id=own.couple_id)
        .select_related("user").order_by("id")[:3])
    if not _valid_members(own.couple.couple_connection, members, user):
        return {}
    other = next(m for m in members if m.user_id != user.pk)
    return {other.user_id: own.partner_nickname}


@transaction.atomic
def set_partner_nickname(user, *, connection_id, expected_revision, expected_nickname_revision, nickname, notify_partner=False):
    nickname = normalize_nickname(nickname)
    snapshot = CoupleConnectionModel.objects.filter(pk=connection_id).first()
    if snapshot is None or user.phone_number not in {snapshot.sender_number, snapshot.receiver_number}:
        raise NotFound()
    # Match accept/end_connection lock order to serialize against unlinking.
    users = list(UserModel.objects.select_for_update().filter(
        phone_number__in=[snapshot.sender_number, snapshot.receiver_number]).order_by("id"))
    connection = CoupleConnectionModel.objects.select_for_update().get(pk=connection_id)
    if ({connection.sender_number, connection.receiver_number} != {u.phone_number for u in users}
            or len(users) != 2 or not all(u.is_active for u in users)
            or user.pk not in {u.pk for u in users}
            or connection.connection_status != CoupleConnectionStatus.ACCEPTED
            or connection.revision != expected_revision):
        raise NicknameConflict()
    couple = CoupleModel.objects.select_for_update().filter(couple_connection=connection).first()
    if couple is None:
        raise NicknameConflict()
    members = list(CoupleMembershipModel.objects.select_for_update(of=("self",))
        .select_related("user").filter(couple=couple).order_by("id"))
    if not _valid_members(connection, members, user):
        raise NicknameConflict()
    own = next(m for m in members if m.user_id == user.pk)
    # Lost response replays are safe, but differing concurrent writes cannot overwrite.
    if own.partner_nickname == nickname and expected_nickname_revision <= own.partner_nickname_revision:
        return own
    if own.partner_nickname_revision != expected_nickname_revision:
        raise NicknameConflict()
    previous = own.partner_nickname
    own.partner_nickname = nickname
    own.partner_nickname_revision += 1
    own.save(update_fields=["partner_nickname", "partner_nickname_revision"])
    if notify_partner is True and nickname:
        from notifications.services import partner_event
        partner_event("nickname.changed", user, couple, own.pk,
            f"nickname:{own.pk}:{own.partner_nickname_revision}", version=own.partner_nickname_revision,
            context={"actor_id": user.pk, "action": "change" if previous else "set"})
    return own
