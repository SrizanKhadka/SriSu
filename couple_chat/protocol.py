"""Authoritative encrypted-transport commands.

Lock order: both account rows (ascending), current login sessions (ascending),
then room. Account locks also serialize device registration and replacement.
No crypto secrets, plaintext, or client-supplied actor identifiers are accepted.
"""
import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from authentication.models import DeviceSession, UserModel
from .models import BundleClaim, Change, Device, KeyPublication, Message, MutationHead, Operation, PreKey, Room
from .services import append_change, available_rooms, room_for


class Conflict(APIException):
    status_code = 409

    def __init__(self, code, message):
        super().__init__({"code": code, "detail": message}, code=code)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def lock_accounts(ids):
    users = list(UserModel.objects.select_for_update().filter(pk__in=ids, is_active=True).order_by("pk"))
    if len(users) != len(set(ids)):
        raise PermissionDenied()


def current_session(user, sid):
    session = DeviceSession.objects.select_for_update().filter(
        pk=sid, user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
    if session is None:
        raise PermissionDenied("Sign in again.")
    return session


def live_devices():
    return Device.objects.filter(revoked_at__isnull=True, session__revoked_at__isnull=True,
                                 session__expires_at__gt=timezone.now(), user__is_active=True)


def device_info(device, *, own=False):
    if device is None:
        return None
    result = {"id": str(device.pk), "user_id": device.user_id, "registration_id": device.registration_id,
              "identity_key": device.identity_key, "signed_prekey": device.signed_prekey}
    if own:
        result["prekeys_remaining"] = {kind: device.prekeys.filter(kind=kind, consumed_at__isnull=True).count()
                                      for kind in ("ec", "kyber")}
    return result


def device_state(user, sid, room=None):
    own = Device.objects.filter(user=user, revoked_at__isnull=True).first()
    partner = None
    if room:
        partner_id = room.second_id if room.first_id == user.pk else room.first_id
        partner = live_devices().filter(user_id=partner_id).first()
    return {"current_device": device_info(own, own=True),
            "session_owns_device": bool(own and str(own.session_id) == str(sid)),
            "partner_device": device_info(partner)}


def publish_keys(device, data, *, initial=False):
    signed = data["signed_prekey"]
    if not initial and signed != device.signed_prekey and signed["id"] <= device.signed_prekey["id"]:
        raise Conflict("key_id_reused", "Signed prekey identifiers must increase on rotation.")
    for kind, field in (("ec", "ec_prekeys"), ("kyber", "kyber_prekeys")):
        rows = []
        for key in data[field]:
            existing = PreKey.objects.filter(device=device, kind=kind, key_id=key["id"]).first()
            if existing:
                if existing.public_key != key["public_key"] or existing.signature != key.get("signature", ""):
                    raise Conflict("key_id_reused", "A prekey identifier cannot change its key.")
                # An identical consumed prekey is never made available again.
                continue
            rows.append(PreKey(device=device, kind=kind, key_id=key["id"], public_key=key["public_key"],
                               signature=key.get("signature", "")))
        if device.prekeys.filter(kind=kind, consumed_at__isnull=True).count() + len(rows) > 100:
            raise Conflict("key_pool_full", "At most 100 unused prekeys of each kind are allowed.")
        PreKey.objects.bulk_create(rows)
    device.signed_prekey = signed
    device.save(update_fields=["signed_prekey"])


def device_changed(user, device):
    # Account lock precedes room lock, matching relationship and command services.
    for room in available_rooms(user).order_by("pk"):
        room = Room.objects.select_for_update().get(pk=room.pk)
        append_change(room, "device.changed", {"user_id": user.pk, "device_id": str(device.pk)})


@transaction.atomic
def register(user, sid, data):
    lock_accounts([user.pk])
    session = current_session(user, sid)
    fingerprint = digest(data)
    existing = Device.objects.filter(pk=data["device_id"]).first()
    if existing:
        if existing.user_id != user.pk or existing.session_id != session.pk or existing.revoked_at:
            raise Conflict("device_id_unavailable", "Use a new installation identifier.")
        if existing.registration_digest != fingerprint:
            raise Conflict("operation_conflict", "Device registration payload changed.")
        return device_info(existing, own=True)
    active = Device.objects.filter(user=user, revoked_at__isnull=True).first()
    if (active.pk if active else None) != data["replace_device_id"]:
        raise Conflict("replacement_required", "Confirm replacement of the current chat device.")
    if active:
        active.revoked_at = timezone.now()
        active.save(update_fields=["revoked_at"])
    device = Device.objects.create(id=data["device_id"], user=user, session=session,
        registration_id=data["registration_id"], identity_key=data["identity_key"],
        signed_prekey=data["signed_prekey"], registration_digest=fingerprint)
    publish_keys(device, data, initial=True)
    device_changed(user, device)
    return device_info(device, own=True)


def own_device(user, session, device_id):
    device = live_devices().filter(pk=device_id, user=user, session=session).first()
    if device is None:
        raise Conflict("device_revoked", "This login no longer owns the active chat device.")
    return device


@transaction.atomic
def replenish(user, sid, data):
    lock_accounts([user.pk])
    device = own_device(user, current_session(user, sid), data["device_id"])
    fingerprint = digest(data)
    old = KeyPublication.objects.filter(device=device, operation_id=data["operation_id"]).first()
    if old:
        if old.digest != fingerprint:
            raise Conflict("operation_conflict", "The operation identifier has another payload.")
    else:
        publish_keys(device, data)
        KeyPublication.objects.create(device=device, operation_id=data["operation_id"], digest=fingerprint)
    return device_info(device, own=True)


def lock_room(user, sid, room_id, device_id):
    snapshot = room_for(user, room_id)
    lock_accounts([snapshot.first_id, snapshot.second_id])
    # Lock both registered sessions to serialize against refresh-token revocation.
    session_ids = set(Device.objects.filter(user_id__in=[snapshot.first_id, snapshot.second_id],
                                           revoked_at__isnull=True).values_list("session_id", flat=True))
    from uuid import UUID
    session_ids.add(UUID(str(sid)))
    list(DeviceSession.objects.select_for_update().filter(pk__in=session_ids).order_by("pk"))
    session = current_session(user, sid)
    room = Room.objects.select_for_update().get(pk=snapshot.pk)
    room_for(user, room.pk)  # Recheck after the locks, including current membership/block state.
    device = own_device(user, session, device_id)
    return room, device


def partner_device(user, room, device_id):
    partner_id = room.second_id if room.first_id == user.pk else room.first_id
    device = live_devices().filter(pk=device_id, user_id=partner_id).first()
    if not device:
        raise Conflict("partner_device_changed", "Refresh and verify your partner's current device.")
    return device


@transaction.atomic
def claim_bundle(user, sid, room_id, data):
    room, device = lock_room(user, sid, room_id, data["device_id"])
    recipient = partner_device(user, room, data["recipient_device_id"])
    old = BundleClaim.objects.filter(room=room, requester=device, operation_id=data["operation_id"]).first()
    if old:
        if old.recipient_id != recipient.pk:
            raise Conflict("operation_conflict", "Bundle claim recipient changed.")
        return old.bundle
    # Includes separate per-Spark sessions. Keep idempotent claim records, while
    # bounding new prekey consumption per device pair in a rolling day.
    if BundleClaim.objects.filter(room=room, requester=device, recipient=recipient,created_at__gte=timezone.now()-timedelta(days=1)).count() >= 20:
        raise Conflict("session_reset_limit", "Secure session setup is limited to 20 new sessions per day. Try again later.")
    selected = {kind: recipient.prekeys.filter(kind=kind, consumed_at__isnull=True).order_by("key_id").first()
                for kind in ("ec", "kyber")}
    if any(key is None for key in selected.values()):
        raise Conflict("prekeys_exhausted", "Your partner must open SriSu to replenish secure session keys.")
    bundle = device_info(recipient)
    for kind, key in selected.items():
        bundle[kind + "_prekey"] = {"id": key.key_id, "public_key": key.public_key}
        if kind == "kyber":
            bundle[kind + "_prekey"]["signature"] = key.signature
        key.consumed_at = timezone.now()
        key.save(update_fields=["consumed_at"])
    BundleClaim.objects.create(room=room, requester=device, recipient=recipient,
                               operation_id=data["operation_id"], bundle=bundle)
    return bundle


def operation_result(operation):
    return {"operation_id": str(operation.operation_id), "sequence": operation.change.sequence,
            "applied": operation.applied, "accepted_at": operation.change.created_at.isoformat()}


@transaction.atomic
def accept_operation(user, sid, room_id, data):
    room, device = lock_room(user, sid, room_id, data["device_id"])
    fingerprint = digest({key:value for key,value in data.items() if key != "attachments" or value})
    existing = Operation.objects.select_related("change").filter(room=room, actor=user,
        operation_id=data["operation_id"]).first()
    if existing:
        if existing.digest != fingerprint:
            raise Conflict("operation_conflict", "The operation identifier has another payload.")
        return operation_result(existing)
    recipient = partner_device(user, room, data["recipient_device_id"])
    # Limit a single account's outstanding ciphertext backlog (maximum ~64 MiB).
    pending = Operation.objects.filter(room=room, sender_device=device, kind="message").exclude(
        target_id__in=MutationHead.objects.filter(actor_id=recipient.user_id,
            kind__in=["receipt.delivered", "receipt.read", "receipt.played"]).values("message_id"))
    if data["kind"] == "message" and pending.count() >= 1000:
        raise Conflict("recipient_backlog_full", "Wait for your partner to receive queued messages.")
    applied = True
    if data["kind"] == "message":
        if Message.objects.filter(pk=data["target_id"]).exists():
            raise Conflict("message_id_unavailable", "Use a new message identifier.")
        reply = None
        if data["reply_to"]:
            # A concurrent deletion leaves a valid same-room tombstone. Clients
            # suppress its quoted preview; it must not strand an offline reply.
            reply = Message.objects.filter(pk=data["reply_to"], room=room).first()
            if not reply:
                raise NotFound()
        message = Message.objects.create(id=data["target_id"], room=room, author=user, device=device, reply_to=reply)
        from .media import bind
        bind(message,user,device,data.get("attachments",[]))
    else:
        target = Message.objects.filter(pk=data["target_id"], room=room).first()
        if not target:
            raise NotFound()
        if data["kind"] == "delete":
            if target.author_id != user.pk:
                raise PermissionDenied("Only the sender can delete for everyone.")
            applied = target.deleted_at is None
            if applied:
                target.deleted_at = timezone.now()
                target.save(update_fields=["deleted_at"])
                from .media import tombstone
                tombstone(target)
                # Independent Spark sessions do not need held envelopes to
                # advance ordinary chat. Erase them with the card tombstone.
                from .models import SparkAnswer
                SparkAnswer.objects.filter(spark_id=target.pk).update(envelope={})
        elif data["kind"].startswith("receipt."):
            if target.author_id == user.pk:
                raise PermissionDenied("Only the recipient can acknowledge a message.")
        elif target.deleted_at:
            raise Conflict("message_deleted", "The message has been deleted.")
        if data["kind"] != "delete":
            head = MutationHead.objects.filter(message=target, actor=user, kind=data["kind"]).first()
            applied = head is None or head.device_id != device.pk or head.version < data["version"]
            if applied:
                MutationHead.objects.update_or_create(message=target, actor=user, kind=data["kind"],
                    defaults={"device": device, "version": data["version"]})
    change = append_change(room, "operation")
    operation = Operation.objects.create(room=room, actor=user, operation_id=data["operation_id"],
        change=change, sender_device=device, recipient_device=recipient, kind=data["kind"],
        target_id=data["target_id"], version=data["version"], applied=applied,
        signal_type=data["signal_type"], ciphertext=data["ciphertext"], digest=fingerprint,
        attachment_ids=[str(value) for value in data.get("attachments",[])])
    return operation_result(operation)


@transaction.atomic
def synchronize(user, sid, room_id, data):
    room, device = lock_room(user, sid, room_id, data["device_id"])
    if data["after"] > room.sequence:
        raise Conflict("cursor_ahead", "The cursor belongs to another history. Reconcile from the beginning.")
    rows = list(Change.objects.filter(room=room, sequence__gt=data["after"])
                .select_related("operation").order_by("sequence")[:data["limit"]])
    targets = [row.operation.target_id for row in rows if hasattr(row, "operation")]
    messages = {message.pk: message for message in Message.objects.filter(pk__in=targets)}
    changes = []
    for row in rows:
        result = {"sequence": row.sequence, "kind": row.kind, "created_at": row.created_at.isoformat(),
                  "metadata": row.metadata, "operation": None}
        if hasattr(row, "operation"):
            op = row.operation
            target = messages.get(op.target_id)
            accessible = device.pk in (op.sender_device_id, op.recipient_device_id)
            result["operation"] = {"operation_id": str(op.operation_id), "actor_id": op.actor_id,
                "sender_device_id": str(op.sender_device_id), "recipient_device_id": str(op.recipient_device_id),
                "kind": op.kind, "target_id": str(op.target_id), "version": op.version, "applied": op.applied,
                "signal_type": op.signal_type, "ciphertext": op.ciphertext if accessible else None,
                "attachments": op.attachment_ids,
                "unavailable": not accessible, "target_deleted": bool(target and target.deleted_at),
                "reply_to": str(target.reply_to_id) if op.kind == "message" and target and target.reply_to_id else None}
        changes.append(result)
    cursor = rows[-1].sequence if rows else data["after"]
    return {"changes": changes, "cursor": cursor, "high_watermark": room.sequence, "has_more": cursor < room.sequence}
