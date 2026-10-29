"""Authoritative personal Faves; no notification, connection or chat side effects."""
from django.db import transaction
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, ValidationError

from authentication.models import UserModel
from social.models import CoupleFaveModel, CoupleFaveVersion, CoupleMembershipModel
from social.services.moment_service import active_couples, blocked_user_ids


def available_couples(user):
    blocked = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user), ended_at__isnull=True).values("couple_id")
    own = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values("couple_id")
    return active_couples().exclude(pk__in=blocked).exclude(pk__in=own)


def fave_state(user):
    rows = CoupleFaveModel.objects.filter(user=user)
    summary = rows.aggregate(count=Count("id"), latest=Max("id"))
    return rows.values_list("couple_id", flat=True), f"{fave_version(user)}:{summary['count']}:{summary['latest']}"


def fave_version(user):
    return CoupleFaveVersion.objects.filter(user=user).values_list("version", flat=True).first() or 0


class FaveConflict(APIException):
    status_code = 409
    default_detail = "Faves changed. Check the current state before retrying."
    default_code = "fave_version_changed"


def mutation_version(user, expected):
    state, _ = CoupleFaveVersion.objects.get_or_create(user=user)
    if expected is not None and state.version != expected:
        raise FaveConflict()
    return state


def advance(state):
    state.version += 1
    state.save(update_fields=["version"])


@transaction.atomic
def add_fave(user, couple_id, expected=None):
    # Serializes contradictory add/remove requests, including when no Fave exists yet.
    UserModel.objects.select_for_update().get(pk=user.pk)
    state = mutation_version(user, expected)
    if CoupleMembershipModel.objects.filter(user=user, couple_id=couple_id, ended_at__isnull=True).exists():
        raise ValidationError({"couple": "You cannot add your own couple to Faves."})
    couple = get_object_or_404(available_couples(user), pk=couple_id)
    result = CoupleFaveModel.objects.get_or_create(user=user, couple=couple)
    # Even a conditional no-op is a barrier against an older, delayed opposite write.
    if result[1] or expected is not None:
        advance(state)
    return result


@transaction.atomic
def remove_fave(user, couple_id, expected=None):
    UserModel.objects.select_for_update().get(pk=user.pk)
    state = mutation_version(user, expected)
    count, _ = CoupleFaveModel.objects.filter(user=user, couple_id=couple_id).delete()
    if count or expected is not None:
        advance(state)
