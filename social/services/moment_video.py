"""Resumable, private video staging. All media work stays outside row locks."""
import hashlib
import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from social.models import MomentDraftPhoto, MomentVideo, MomentVideoFile
from social.services.moment_publication import draft_for, MomentConflict
from social.services.moment_service import compensate_upload

CHUNK_BYTES = 2 * 1024 * 1024
MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION_MS = 60_000


def video_data(video):
    return {"id": str(video.pk), "state": video.state, "source_bytes": video.source_bytes,
        "received_bytes": video.received_bytes, "chunk_bytes": CHUNK_BYTES,
        "duration_ms": video.duration_ms, "width": video.width, "height": video.height,
        "error_code": video.error_code}


def owned_video(user, draft_id, video_id, *, lock=False):
    draft = draft_for(user, draft_id, lock=lock, editable=True)
    photo = draft.photos.filter(pk=video_id, video_id=video_id).first()
    if not photo:
        raise Http404
    query = MomentVideo.objects.select_for_update() if lock else MomentVideo.objects
    return draft, query.get(pk=video_id, owner=user)


@transaction.atomic
def begin_video(user, draft_id, video_id, source_bytes):
    draft = draft_for(user, draft_id, lock=True, editable=True)
    existing = MomentVideo.objects.filter(pk=video_id).first()
    if existing:
        _, existing = owned_video(user, draft_id, video_id)
        if existing.source_bytes != source_bytes:
            raise MomentConflict()
        return existing
    if MomentDraftPhoto.objects.filter(pk=video_id).exists():
        raise MomentConflict()
    if draft.photos.count() >= 5:
        raise ValidationError({"media": "Add up to five photos or videos."})
    video = MomentVideo.objects.create(id=video_id, owner=user, source_bytes=source_bytes)
    MomentDraftPhoto.objects.create(id=video_id, draft=draft, video=video,
        image="", digest="", width=0, height=0)
    draft.revision += 1
    draft.save(update_fields=["revision"])
    return video


def upload_chunk(user, draft_id, video_id, offset, upload):
    _, video = owned_video(user, draft_id, video_id)
    expected = min(CHUNK_BYTES, video.source_bytes - offset)
    if offset < 0 or offset % CHUNK_BYTES or expected <= 0 or not upload or upload.size != expected:
        raise ValidationError({"chunk": "Send the next complete upload chunk."})
    # The parser and request boundary cap the body; never read an entire video here.
    data = upload.read(CHUNK_BYTES + 1)
    if len(data) != expected:
        raise ValidationError({"chunk": "The upload chunk is incomplete."})
    digest = hashlib.sha256(data).hexdigest()
    key = f"upload_{offset // CHUNK_BYTES:04d}"
    existing = video.files.filter(name=key).first()
    if existing:
        if existing.digest != digest:
            raise MomentConflict()
        return video
    if video.state != "uploading" or offset != video.received_bytes:
        raise MomentConflict()
    storage = MomentVideoFile._meta.get_field("file").storage
    name = f"couples/moments/videos/{video.pk}/{uuid.uuid4().hex}.part"
    saved = name
    retained = False
    try:
        saved = storage.save(name, ContentFile(data))
        with transaction.atomic():
            _, video = owned_video(user, draft_id, video_id, lock=True)
            existing = video.files.filter(name=key).first()
            if existing:
                if existing.digest != digest:
                    raise MomentConflict()
            else:
                if video.state != "uploading" or offset != video.received_bytes:
                    raise MomentConflict()
                MomentVideoFile.objects.create(video=video, name=key, file=saved, size=len(data), digest=digest)
                video.received_bytes += len(data)
                video.updated_at = timezone.now()
                video.save(update_fields=["received_bytes", "updated_at"])
                retained = True
        return video
    finally:
        if not retained:
            compensate_upload(saved)
        if saved != name:
            compensate_upload(name)


@transaction.atomic
def finish_video(user, draft_id, video_id):
    _, video = owned_video(user, draft_id, video_id, lock=True)
    if video.received_bytes != video.source_bytes:
        raise ValidationError({"video": "Finish uploading the video first."})
    if video.state == "uploading":
        video.state = "queued"
        video.updated_at = timezone.now()
        video.save(update_fields=["state", "updated_at"])
    return video


@transaction.atomic
def retry_video(user, draft_id, video_id):
    _, video = owned_video(user, draft_id, video_id, lock=True)
    if video.state == "failed" and video.error_code == "processing" and video.attempts < 3:
        video.state, video.error_code, video.lease = "queued", "", None
        video.updated_at = timezone.now()
        video.save(update_fields=["state", "error_code", "lease", "updated_at"])
    return video
