"""Owned photo staging and retry-safe publication into the existing Moment model.

Decode and storage I/O precede short database transactions. Couple/draft locks
serialize attachment limits and publication; no external work runs under locks.
"""
import hashlib
import json
import uuid
import warnings
from datetime import timedelta
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile
from django.db import transaction, IntegrityError, DatabaseError
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from social.models import (MomentDraft, MomentDraftPhoto, CoupleMembershipModel,
    CoupleMomentModel, CoupleMomentPhotoModel)
from social.services.moment_service import (active_couples, blocked_user_ids,
    lock_couple, compensate_upload, eligible_moments)


class MomentConflict(APIException):
    status_code = 409
    default_code = "moment_changed"
    default_detail = "This Moment changed. Refresh before trying again."


class MomentExpired(APIException):
    status_code = 410
    default_code = "moment_expired"
    default_detail = "This Moment is no longer available."


def clean_photo(upload):
    if not upload or upload.size > 10 * 1024 * 1024:
        raise ValidationError({"photo": "Choose an image no larger than 10 MiB."})
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as source:
                if (source.format not in {"JPEG", "PNG", "WEBP"} or
                        getattr(source, "n_frames", 1) != 1 or
                        source.width * source.height > 40_000_000 or
                        max(source.size) > 12000):
                    raise ValueError()
                source.load()
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
                # Re-encoding drops EXIF/GPS, ICC/comments and arbitrary embedded metadata.
                clean = Image.new("RGB", oriented.size, "white")
                if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean.paste(oriented.convert("RGB"))
                output = BytesIO()
                clean.save(output, format="JPEG", quality=90)
                result = ContentFile(output.getvalue(), name="moment.jpg")
                result.moment_dimensions = clean.size
                return result
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValidationError({"photo": "Choose a valid, non-animated JPEG, PNG or WebP image."})


def current_couple(user, couple_id, *, lock=False):
    if lock:
        lock_couple(couple_id)
    blocked = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user),
        ended_at__isnull=True).values("couple_id")
    couple = active_couples().exclude(pk__in=blocked).filter(pk=couple_id).first()
    if not couple:
        raise Http404
    members = list(couple.memberships.filter(ended_at__isnull=True).order_by("id"))
    if user.pk not in [m.user_id for m in members]:
        raise Http404
    return couple, members


def draft_for(user, draft_id, *, lock=False, editable=False):
    try:
        draft_id = uuid.UUID(str(draft_id))
    except (ValueError, TypeError):
        raise Http404
    initial = MomentDraft.objects.filter(pk=draft_id, owner=user).first()
    if not initial:
        raise Http404
    _, members = current_couple(user, initial.couple_id, lock=lock)
    draft = MomentDraft.objects.select_for_update().filter(pk=draft_id).first() if lock else initial
    if draft is None:
        raise Http404
    if (draft.audience_membership_ids != [m.pk for m in members] or
            draft.audience_user_ids != [m.user_id for m in members]):
        raise Http404
    if draft.published_at is None and draft.created_at <= timezone.now() - timedelta(days=7):
        raise MomentExpired()
    if draft.published_at and not eligible_moments(user).filter(pk=draft.moment_id).exists():
        raise MomentExpired()
    if editable and draft.published_at is not None:
        raise MomentConflict()
    return draft


@transaction.atomic
def create_draft(user, request_id, couple_id):
    couple, members = current_couple(user, couple_id, lock=True)
    existing = MomentDraft.objects.filter(pk=request_id).first()
    if existing:
        if existing.owner_id != user.pk or existing.couple_id != couple_id:
            raise Http404
        return draft_for(user, request_id), False
    if MomentDraft.objects.filter(owner=user, published_at=None,
            created_at__gt=timezone.now() - timedelta(days=7)).count() >= 10:
        raise ValidationError({"draft": "Finish or discard an existing draft first."})
    return MomentDraft.objects.create(id=request_id, owner=user, couple=couple,
        audience_membership_ids=[m.pk for m in members],
        audience_user_ids=[m.user_id for m in members]), True


def upload_photo(user, draft_id, photo_id, upload):
    draft_for(user, draft_id, editable=True)
    clean = clean_photo(upload)
    digest = hashlib.sha256(clean.read()).hexdigest()
    clean.seek(0)
    storage = MomentDraftPhoto._meta.get_field("image").storage
    name = f"couples/moments/{uuid.uuid4().hex}.jpg"
    saved = name
    retained = False
    try:
        saved = storage.save(name, clean)
        with transaction.atomic():
            draft = draft_for(user, draft_id, lock=True, editable=True)
            existing = MomentDraftPhoto.objects.filter(pk=photo_id).first()
            if existing:
                if existing.draft_id != draft.id:
                    raise Http404
                if existing.digest != digest:
                    raise MomentConflict()
            else:
                if draft.photos.count() >= 5:
                    raise ValidationError({"photos": "A maximum of five photos is allowed."})
                MomentDraftPhoto.objects.create(id=photo_id, draft=draft, image=saved,
                    digest=digest, width=clean.moment_dimensions[0], height=clean.moment_dimensions[1])
                draft.revision += 1
                draft.save(update_fields=["revision"])
                inserted = True
            if existing:
                inserted = False
        retained = inserted
        return draft
    except IntegrityError:
        raise MomentConflict()
    finally:
        if not retained:
            compensate_upload(saved)
        if saved != name:
            compensate_upload(name)


def publication_hash(data):
    payload = {"caption": data["caption"], "visibility": data["visibility"],
        "photo_ids": [str(p) for p in data["photo_ids"]]}
    # Keep hashes for every released no-crop client and already published draft unchanged.
    if data.get("crops"):
        payload["crops"] = data["crops"]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def replay(user, draft, fingerprint):
    if draft.published_at:
        if fingerprint != draft.publication_hash:
            raise MomentConflict()
        moment = eligible_moments(user).filter(pk=draft.moment_id).first()
        if moment is None:
            raise MomentExpired()
        return moment


def publication_photos(draft, data):
    if data["expected_revision"] != draft.revision:
        raise MomentConflict()
    photos = {p.id: p for p in draft.photos.all()}
    order = data["photo_ids"]
    if not 1 <= len(order) <= 5 or len(set(order)) != len(order) or set(order) != set(photos):
        raise ValidationError({"photo_ids": "Choose the one to five photos uploaded to this draft."})
    if not set(data.get("crops", {})).issubset({str(pk) for pk in photos}):
        raise ValidationError({"crops": "Choose photos uploaded to this draft."})
    return photos


def crop_photo(photo, crop):
    """Crop normalized, orientation-corrected staging pixels; never enlarge them."""
    with photo.image.storage.open(photo.image.name, "rb") as source, Image.open(source) as image:
        # Match the preview's positive rounding and one-pixel minimum for tiny inputs.
        left = min(image.width - 1, int(crop["x"] * image.width + .5))
        top = min(image.height - 1, int(crop["y"] * image.height + .5))
        right = max(left + 1, min(image.width, int((crop["x"] + crop["width"]) * image.width + .5)))
        bottom = max(top + 1, min(image.height, int((crop["y"] + crop["height"]) * image.height + .5)))
        with image.crop((left, top, right, bottom)) as cropped:
            output = BytesIO()
            cropped.save(output, format="JPEG", quality=95, subsampling=0)
            result = ContentFile(output.getvalue(), name="moment.jpg")
            result.moment_dimensions = cropped.size
            return result


def publish(user, draft_id, data):
    fingerprint = publication_hash(data)
    if not data.get("crops"):
        # Preserve the original single-transaction path for all full-photo/legacy clients.
        with transaction.atomic():
            draft = draft_for(user, draft_id, lock=True)
            existing = replay(user, draft, fingerprint)
            if existing:
                return existing, False
            photos = publication_photos(draft, data)
            return commit_publication(user, draft, data, fingerprint, photos, {}), True
    draft = draft_for(user, draft_id)
    existing = replay(user, draft, fingerprint)
    if existing:
        return existing, False
    try:
        photos = publication_photos(draft, data)
    except ValidationError:
        # An identical publication can remove staging between the two preflight reads.
        existing = replay(user, draft_for(user, draft_id), fingerprint)
        if existing:
            return existing, False
        raise
    storage = MomentDraftPhoto._meta.get_field("image").storage
    prepared, saved_names = {}, set()
    retained = False
    try:
        # Decode/storage outside locks. The commit rechecks membership, revision and IDs.
        for key, crop in data.get("crops", {}).items():
            pk = uuid.UUID(key)
            clean = crop_photo(photos[pk], crop)
            name = f"couples/moments/{uuid.uuid4().hex}.jpg"
            saved_names.add(name)  # Also compensate storage that writes then raises.
            saved = storage.save(name, clean)
            saved_names.add(saved)
            prepared[pk] = (saved, *clean.moment_dimensions)
        with transaction.atomic():
            draft = draft_for(user, draft_id, lock=True)
            existing = replay(user, draft, fingerprint)
            if existing:
                return existing, False
            photos = publication_photos(draft, data)
            moment = commit_publication(user, draft, data, fingerprint, photos, prepared)
        retained = True
        return moment, True
    except OSError:
        # Another identical request may have committed and removed the staging source.
        existing = replay(user, draft_for(user, draft_id), fingerprint)
        if existing:
            return existing, False
        raise ValidationError({"photos": "The photo could not be prepared. Your draft is kept; try again."})
    finally:
        kept_names = {photo[0] for photo in prepared.values()} if retained else set()
        for name in saved_names - kept_names:
            # An on_commit cleanup callback can fail after publication committed. Never
            # compensate a file now owned by the published Moment. On DB uncertainty,
            # leave the file for the existing orphan sweeper instead of risking data loss.
            try:
                if CoupleMomentPhotoModel.objects.filter(image=name).exists():
                    continue
            except DatabaseError:
                continue
            compensate_upload(name)


def commit_publication(user, draft, data, fingerprint, photos, prepared):
    moment = CoupleMomentModel.objects.create(couple_id=draft.couple_id, created_by=user,
        audience_membership_ids=draft.audience_membership_ids, audience_user_ids=draft.audience_user_ids,
        caption=data["caption"], visibility=data["visibility"], moment_date=timezone.localdate())
    rows = []
    for i, pk in enumerate(data["photo_ids"]):
        name, width, height = prepared.get(pk, (photos[pk].image.name, photos[pk].width, photos[pk].height))
        rows.append(CoupleMomentPhotoModel(moment=moment, image=name, order=i, width=width, height=height))
    CoupleMomentPhotoModel.objects.bulk_create(rows)
    draft.published_at = moment.created_at
    draft.moment = moment
    draft.publication_hash = fingerprint
    draft.save(update_fields=["published_at", "moment", "publication_hash"])
    # Existing published photo rows now own the files. The deletion guard sees them.
    draft.photos.all().delete()
    return moment
