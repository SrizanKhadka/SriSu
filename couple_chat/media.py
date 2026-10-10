"""Private, immutable ciphertext uploads on the existing Django storage backend."""
from datetime import timedelta
import hashlib
import logging

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import FileResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from .models import Attachment, FileCleanup, Message, Operation
from .protocol import Conflict, lock_room

MAX_CIPHERTEXT = 16 * 1024 * 1024 + 28
CAPABILITY_SALT = "srisu.couple-chat.media.v1"
logger = logging.getLogger("srisu.couple_chat")


def storage_name(room_id, attachment_id):
    return f"couples/couple_chat_private/{room_id}/{attachment_id}.bin"


def describe(attachment):
    return {"id": str(attachment.pk), "message_id": str(attachment.intended_message_id),
        "ciphertext_size": attachment.ciphertext_size, "sha256": attachment.sha256,
        "state": "deleted" if attachment.deleted_at else "attached" if attachment.message_id else
                 "ready" if attachment.ready_at else "uploaded" if attachment.uploaded_at else "pending",
        "expires_at": attachment.expires_at.isoformat() if attachment.message_id is None else None}


def writable(user, room, device, attachment_id):
    attachment = Attachment.objects.select_for_update().filter(pk=attachment_id, room=room, owner=user, device=device).first()
    if attachment is None:
        raise NotFound()
    if attachment.deleted_at or (attachment.message_id is None and attachment.expires_at <= timezone.now()):
        raise Conflict("attachment_unavailable", "Select the attachment again.")
    return attachment


@transaction.atomic
def initialize(user, sid, room_id, data):
    room, device = lock_room(user, sid, room_id, data["device_id"])
    existing = Attachment.objects.filter(pk=data["attachment_id"]).first()
    if existing:
        if existing.room_id != room.pk or existing.owner_id != user.pk or existing.device_id != device.pk:
            raise NotFound()
        if (existing.intended_message_id, existing.ciphertext_size, existing.sha256) != (data["message_id"], data["ciphertext_size"], data["sha256"]):
            raise Conflict("operation_conflict", "Attachment content changed.")
        return describe(writable(user,room,device,existing.pk))
    if Message.objects.filter(pk=data["message_id"]).exists():
        raise Conflict("message_id_unavailable", "Attachments must be prepared before sending.")
    now = timezone.now()
    available = Attachment.objects.filter(room=room, deleted_at__isnull=True).filter(Q(message__isnull=False) | Q(expires_at__gt=now))
    used = available.aggregate(total=Sum("ciphertext_size"))["total"] or 0
    quota = getattr(settings,"COUPLE_CHAT_ROOM_MEDIA_BYTES",512 * 1024 * 1024)
    if used + data["ciphertext_size"] > quota or available.filter(owner=user,message__isnull=True).count() >= 8:
        raise Conflict("attachment_quota", "This conversation’s media allowance is full. Remove attachments or finish pending sends.")
    try:
        with transaction.atomic():
            attachment = Attachment.objects.create(id=data["attachment_id"],room=room,owner=user,device=device,
                intended_message_id=data["message_id"],ciphertext_size=data["ciphertext_size"],sha256=data["sha256"],
                blob=storage_name(room.pk,data["attachment_id"]),expires_at=now+timedelta(hours=24))
    except IntegrityError:
        if Attachment.objects.filter(pk=data["attachment_id"]).exists():
            raise NotFound() from None
        raise
    return describe(attachment)


@transaction.atomic
def upload(user, sid, room_id, attachment_id, device_id, ciphertext):
    room,device = lock_room(user,sid,room_id,device_id)
    attachment = writable(user,room,device,attachment_id)
    if len(ciphertext) != attachment.ciphertext_size or hashlib.sha256(ciphertext).hexdigest() != attachment.sha256:
        raise Conflict("attachment_integrity", "The encrypted upload does not match its reservation.")
    if attachment.uploaded_at:
        return describe(attachment)
    # Opaque byte uploads use the existing storage backend, never its public URL.
    name = storage_name(room.pk,attachment.pk)
    if default_storage.exists(name):
        # Recover a crash after file persistence but before the DB commit. Never
        # create suffixed copies or overwrite an immutable reservation.
        with default_storage.open(name,"rb") as prior:
            recovered=prior.read(MAX_CIPHERTEXT+1)
        if len(recovered)!=attachment.ciphertext_size or hashlib.sha256(recovered).hexdigest()!=attachment.sha256:
            raise Conflict("attachment_integrity","Stored ciphertext does not match the reservation.")
        saved=name
    else:
        saved = default_storage.save(name,ContentFile(ciphertext))
    try:
        attachment.blob.name=saved
        attachment.uploaded_at=timezone.now()
        attachment.save(update_fields=["blob","uploaded_at"])
    except Exception:
        default_storage.delete(saved)
        raise
    return describe(attachment)


@transaction.atomic
def finalize(user,sid,room_id,attachment_id,device_id):
    room,device=lock_room(user,sid,room_id,device_id)
    attachment=writable(user,room,device,attachment_id)
    if not attachment.uploaded_at or not attachment.blob or not default_storage.exists(attachment.blob.name):
        raise Conflict("attachment_incomplete","Upload the encrypted attachment before finalizing.")
    if not attachment.ready_at:
        attachment.ready_at=timezone.now()
        attachment.save(update_fields=["ready_at"])
    return describe(attachment)


def bind(message,user,device,ids):
    rows=list(Attachment.objects.select_for_update().filter(pk__in=ids,room=message.room,owner=user,device=device,
        intended_message_id=message.pk,message__isnull=True,ready_at__isnull=False,deleted_at__isnull=True,expires_at__gt=timezone.now()))
    if len(rows)!=len(ids):
        raise Conflict("attachment_incomplete","An attachment is not ready for this message.")
    Attachment.objects.filter(pk__in=ids).update(message=message)


def readable(user,sid,room_id,attachment_id,device_id):
    room,device=lock_room(user,sid,room_id,device_id)
    attachment=Attachment.objects.filter(pk=attachment_id,room=room,deleted_at__isnull=True,
        message__isnull=False,message__deleted_at__isnull=True,ready_at__isnull=False).first()
    if attachment is None or not Operation.objects.filter(room=room,operation_id=attachment.message_id,kind="message").filter(
        Q(sender_device=device)|Q(recipient_device=device)).exists():
        raise NotFound()
    return attachment


@transaction.atomic
def capability(user,sid,room_id,attachment_id,device_id):
    attachment=readable(user,sid,room_id,attachment_id,device_id)
    token=signing.dumps({"user":user.pk,"sid":str(sid),"device":str(device_id),"attachment":str(attachment.pk)},salt=CAPABILITY_SALT)
    return {"capability":token,"expires_in":60}


@transaction.atomic
def download(user,sid,room_id,attachment_id,token):
    try:
        value=signing.loads(token,salt=CAPABILITY_SALT,max_age=60)
        if value["user"]!=user.pk or value["sid"]!=str(sid) or value["attachment"]!=str(attachment_id):
            raise signing.BadSignature()
    except (signing.BadSignature,KeyError,TypeError,ValueError):
        raise PermissionDenied("Request a fresh media capability.") from None
    attachment=readable(user,sid,room_id,attachment_id,value["device"])
    try: content=default_storage.open(attachment.blob.name,"rb")
    except FileNotFoundError: raise NotFound() from None
    response=FileResponse(content,content_type="application/octet-stream")
    response["Content-Length"]=str(attachment.ciphertext_size)
    response["Cache-Control"]="private, no-store"
    response["X-Content-Type-Options"]="nosniff"
    return response


def tombstone(message):
    ids=list(Attachment.objects.filter(message=message,deleted_at__isnull=True).values_list("pk",flat=True))
    Attachment.objects.filter(pk__in=ids).update(deleted_at=timezone.now())
    transaction.on_commit(lambda: cleanup_ids(ids))


def cleanup_ids(ids):
    # Row tombstones revoke access first. A storage error leaves a retryable job.
    for attachment in Attachment.objects.filter(pk__in=ids,deleted_at__isnull=False):
        try:
            default_storage.delete(attachment.blob.name or storage_name(attachment.room_id,attachment.pk))
            Attachment.objects.filter(pk=attachment.pk).update(blob="")
        except Exception: logger.warning("couple_chat_media_cleanup_failed")


def cleanup_files():
    for row in FileCleanup.objects.order_by("pk")[:100]:
        try:
            default_storage.delete(row.name)
            row.delete()
        except Exception: logger.warning("couple_chat_media_cleanup_failed")


@transaction.atomic
def cancel(user,sid,room_id,attachment_id,device_id):
    room,device=lock_room(user,sid,room_id,device_id)
    attachment=Attachment.objects.select_for_update().filter(pk=attachment_id,room=room,owner=user,device=device).first()
    if attachment is None: raise NotFound()
    if attachment.message_id: raise Conflict("attachment_attached","Delete the message to remove an attached file.")
    if not attachment.deleted_at:
        attachment.deleted_at=timezone.now(); attachment.save(update_fields=["deleted_at"])
    transaction.on_commit(lambda:cleanup_ids([attachment.pk]))
    return describe(attachment)
