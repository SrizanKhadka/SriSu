"""Authoritative personal Faves; no notification, connection or chat side effects."""
import hashlib

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from authentication.models import UserModel
from social.models import CoupleFaveModel, CoupleMembershipModel
from social.services.moment_service import active_couples, blocked_user_ids


def available_couples(user):
    blocked = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user), ended_at__isnull=True).values("couple_id")
    own = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values("couple_id")
    return active_couples().exclude(pk__in=blocked).exclude(pk__in=own)


def fave_state(user):
    rows = list(CoupleFaveModel.objects.filter(user=user).order_by("id").values_list("id", "couple_id"))
    fingerprint = hashlib.sha256(repr(rows).encode()).hexdigest()
    return {couple_id for _, couple_id in rows}, fingerprint


@transaction.atomic
def add_fave(user, couple_id):
    # Serializes contradictory add/remove requests, including when no Fave exists yet.
    UserModel.objects.select_for_update().get(pk=user.pk)
    if CoupleMembershipModel.objects.filter(user=user, couple_id=couple_id, ended_at__isnull=True).exists():
        raise ValidationError({"couple": "You cannot add your own couple to Faves."})
    couple = get_object_or_404(available_couples(user), pk=couple_id)
    return CoupleFaveModel.objects.get_or_create(user=user, couple=couple)


@transaction.atomic
def remove_fave(user, couple_id):
    UserModel.objects.select_for_update().get(pk=user.pk)
    CoupleFaveModel.objects.filter(user=user, couple_id=couple_id).delete()
