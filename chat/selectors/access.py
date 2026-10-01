"""Current relationship authorization shared by HTTP, commands and delivery."""
from django.db.models import Exists, OuterRef, Q

from chat.models import ChatRoom
from social.models import CoupleMembershipModel
from utils.choices import CoupleConnectionStatus


def authorized_rooms(user):
    if not user or not user.is_authenticated or not user.is_active:
        return ChatRoom.objects.none()
    first = CoupleMembershipModel.objects.filter(couple_id=OuterRef("couple_id"), user_id=OuterRef("user_one_id"))
    second = CoupleMembershipModel.objects.filter(couple_id=OuterRef("couple_id"), user_id=OuterRef("user_two_id"))
    return ChatRoom.objects.alias(first_member=Exists(first), second_member=Exists(second)).filter(
        Q(user_one_id=user.pk) | Q(user_two_id=user.pk),
        user_one__is_active=True, user_two__is_active=True,
    ).filter(couple__isnull=False).filter(
        Q(couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
          first_member=True, second_member=True)
    )
