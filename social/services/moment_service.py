"""Moment authorization and transactional media writes."""
import logging
import uuid
from pathlib import Path

from django.db import transaction
from django.db.models import Count, Q, F, BigIntegerField, Prefetch
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound

from social.models import (CoupleModel, CoupleConnectionModel, CoupleMembershipModel, CoupleMomentModel,
                           CoupleMomentPhotoModel, MomentFileDeletion, SingleConnectionModel,
                           CoupleMomentNoteModel)
from utils.choices import CoupleConnectionStatus

logger = logging.getLogger(__name__)


def active_couples():
    return CoupleModel.objects.filter(
        couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
    ).annotate(active_members=Count("memberships", filter=Q(memberships__ended_at__isnull=True, memberships__user__is_active=True) & (
        Q(memberships__user__phone_number=F("couple_connection__sender_number")) |
        Q(memberships__user__phone_number=F("couple_connection__receiver_number"))
    ))).filter(active_members=2)


def blocked_user_ids(user):
    connections = SingleConnectionModel.objects.filter(connection_status="BLOCKED").filter(
        Q(sender_number=user.phone_number) | Q(receiver_number=user.phone_number)
    )
    numbers = set()
    for sender, receiver in connections.values_list("sender_number", "receiver_number"):
        numbers.add(receiver if sender == user.phone_number else sender)
    from authentication.models import UserModel
    return UserModel.objects.filter(phone_number__in=numbers).values_list("id", flat=True)


def eligible_moments(user, at=None):
    """Authorization only, without serialization joins or related-object loading."""
    own_couples = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values("couple_id")
    blocked_couples = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user), ended_at__isnull=True).values("couple_id")
    return (CoupleMomentModel.objects.filter(
        couple_id__in=active_couples().values("id"), expires_at__gt=at or timezone.now(),
        is_archived=False, is_time_capsule=False,
    ).alias(audience_one=Cast(KeyTextTransform("0", "audience_membership_ids"), BigIntegerField()),
            audience_two=Cast(KeyTextTransform("1", "audience_membership_ids"), BigIntegerField()),
            user_one=Cast(KeyTextTransform("0", "audience_user_ids"), BigIntegerField()),
            user_two=Cast(KeyTextTransform("1", "audience_user_ids"), BigIntegerField()))
      .filter(couple__memberships__id=F("audience_one"), couple__memberships__user_id=F("user_one"))
      .filter(couple__memberships__id=F("audience_two"), couple__memberships__user_id=F("user_two"))
      .filter(Q(visibility="public") | Q(couple_id__in=own_couples))
      .exclude(couple_id__in=blocked_couples))


def hydrate_moments(queryset, user):
    return (queryset.annotate(total_view_count=Count("views", distinct=True))
      .select_related("created_by", "couple").prefetch_related("photos", "couple__memberships",
          Prefetch("notes", queryset=visible_notes(user, recipients_only=True), to_attr="appreciation_notes"))
      .order_by("-created_at", "-id"))


def visible_moments(user):
    # Preserve the existing list/detail contract and its private note hydration.
    return hydrate_moments(eligible_moments(user), user)


def visible_notes(user, recipients_only=False):
    """One privacy policy for note endpoints, embedded notes and partner replies."""
    membership = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).first()
    allowed = Q(pk__in=[]) if recipients_only else Q(sender=user)
    if membership:
        allowed |= (Q(moment__couple_id=membership.couple_id,
                      moment__couple_id__in=active_couples().values("id")) &
            (Q(recipient_membership_ids__0=membership.id, recipient_user_ids__0=user.id) |
             Q(recipient_membership_ids__1=membership.id, recipient_user_ids__1=user.id)))
    blocked = list(blocked_user_ids(user))
    return (CoupleMomentNoteModel.objects.filter(allowed).exclude(sender_id__in=blocked).exclude(
        moment__couple__memberships__user_id__in=blocked)
        .prefetch_related("replies").order_by("-created_at", "-id"))


def membership_snapshot(couple):
    return list(couple.memberships.filter(ended_at__isnull=True).order_by("id").values_list("id", flat=True))


def may_modify(moment, user):
    return moment.created_by_id == user.id


def delete_file(name):
    storage = CoupleMomentPhotoModel._meta.get_field("image").storage
    try:
        storage.delete(name)
        MomentFileDeletion.objects.filter(name=name).delete()
    except Exception:
        logger.exception("Moment file deletion will be retried: %s", name)


def queue_file_deletion(name):
    if name:
        MomentFileDeletion.objects.get_or_create(name=name)
        transaction.on_commit(lambda: delete_file(name))


def compensate_upload(name):
    # Attempt storage first, even if a database outage caused the rollback.
    storage = CoupleMomentPhotoModel._meta.get_field("image").storage
    try:
        storage.delete(name)
    except Exception:
        try:
            queue_file_deletion(name)
        except Exception:
            logger.exception("Could not queue orphan %s; run cleanup_moments --sweep-orphans", name)


def lock_couple(couple_id):
    connection_id = CoupleModel.objects.filter(pk=couple_id).values_list("couple_connection_id", flat=True).first()
    if connection_id:
        CoupleConnectionModel.objects.select_for_update().filter(pk=connection_id).first()
    couple = CoupleModel.objects.select_for_update().filter(pk=couple_id).first()
    if couple:
        list(couple.memberships.select_for_update().filter(ended_at__isnull=True).order_by("id"))
    return couple


def save_moment(serializer, user, moment=None):
    """Caller owns the outer atomic block; compensation must run after rollback."""
    data = dict(serializer.validated_data)
    uploads = data.pop("photos", [])
    replace = data.pop("replace_photos", False)
    deleted = data.pop("deleted_photo_ids", [])
    couple_id = moment.couple_id if moment else data.pop("couple", None)
    if couple_id is None:
        couple_id = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values_list("couple_id", flat=True).first()
    # Same parent lock serializes all moment mutations for a couple.
    couple = lock_couple(couple_id)
    if not couple or not active_couples().filter(pk=couple.pk).exists():
        raise ValidationError({"couple": "An active couple with two active partners is required."})
    members = list(couple.memberships.select_for_update().filter(ended_at__isnull=True).order_by("id"))
    if user.id not in [member.user_id for member in members]:
        raise PermissionDenied("You must belong to this couple.")
    if moment:
        if moment.expires_at <= timezone.now():
            raise NotFound()
        if not may_modify(moment, user):
            raise PermissionDenied("Only the creator can modify this moment.")
        if (moment.audience_membership_ids != [member.id for member in members] or
                moment.audience_user_ids != [member.user_id for member in members]):
            raise PermissionDenied("The couple membership has changed.")
    existing = list(moment.photos.all()) if moment else []
    if not set(deleted).issubset({photo.id for photo in existing}):
        raise ValidationError({"deleted_photo_ids": "Unknown photo or photo belonging to another moment."})
    retained = [] if replace else [photo for photo in existing if photo.id not in deleted]
    if len(retained) + len(uploads) > 5:
        raise ValidationError({"photos": "A maximum of five photos is allowed."})
    caption = data.get("caption", moment.caption if moment else "")
    if not caption.strip() and not retained and not uploads:
        raise ValidationError("A caption or at least one photo is required.")
    if moment is None:
        moment = CoupleMomentModel.objects.create(
            couple=couple, created_by=user, audience_membership_ids=[m.id for m in members],
            audience_user_ids=[m.user_id for m in members], **data)
    else:
        data.pop("couple", None)
        for key, value in data.items():
            setattr(moment, key, value)
        moment.save()
    for photo in existing:
        if photo not in retained:
            photo.delete()
    for index, photo in enumerate(retained):
        if photo.order != index:
            photo.order = index
            photo.save(update_fields=["order"])
    for index, upload in enumerate(uploads, start=len(retained)):
        name = f"couples/moments/{uuid.uuid4().hex}{Path(upload.name).suffix.lower()}"
        # Register before writing, including backends that write then raise.
        serializer.context["written_files"].append(name)
        photo = CoupleMomentPhotoModel(moment=moment, order=index)
        stored = photo.image.storage.save(name, upload)
        if stored != name:
            serializer.context["written_files"].append(stored)
        photo.image = stored
        photo.save()
    moment._prefetched_objects_cache = {}
    return moment
