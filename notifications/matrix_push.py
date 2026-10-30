"""Matrix AS ingress: retain routing metadata only, never plaintext or ciphertext.

Only the configured homeserver can call this adapter. Matrix remains the sole
message transport; notification delivery reuses the existing fenced FCM outbox.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from chat.models import MatrixRoomMapping, MatrixUserMapping
from chat.selectors.access import authorized_rooms
from chat.services.matrix_provisioning import matrix_localpart
from social.services.moment_publication import current_couple
from .models import Delivery, Event, Installation, MatrixPushSuppression, MatrixPushTransaction
from .services import digest, enabled_for, installation_cutoff

CAPABILITY = "matrix-chat-push-1"
MAX_AGE = timedelta(minutes=10)


def chat_enabled():
    return settings.NOTIFICATIONS_ENABLED and settings.MATRIX_ENABLED and settings.MATRIX_CHAT_PUSH_ENABLED


def valid_id(value, sigil):
    return isinstance(value, str) and 2 <= len(value) <= 255 and value.startswith(sigil) and not any(
        char.isspace() or ord(char) < 32 for char in value)


def suppression_key(room, event, user=None):
    return digest(f"matrix-push:{room}:{event}:{user if user is not None else '*'}")


def suppressed(event, *, read=False):
    keys = [suppression_key(event.matrix_room_id, event.matrix_event_id)]
    if read:
        keys.append(suppression_key(event.matrix_room_id, event.matrix_event_id, event.recipient_id))
    return MatrixPushSuppression.objects.filter(pk__in=keys).exists()


def chat_destination(event, user):
    try:
        room_id = UUID(event.resource)
    except (ValueError, TypeError):
        raise NotFound() from None
    if not event.matrix_room_id or not event.matrix_event_id or suppressed(event):
        raise NotFound()
    room = authorized_rooms(user).filter(pk=room_id, couple_id=event.couple_id).first()
    mapping = _mapping(event.matrix_room_id)
    if room is None or mapping is None or mapping.chat_room_id != room.pk or mapping.membership_epoch != event.resource_version:
        raise NotFound()
    from django.http import Http404
    try:
        _, members = current_couple(user, room.couple_id)
    except Http404:
        raise NotFound() from None
    if [member.pk for member in members] != event.audience_membership_ids:
        raise NotFound()
    return {"kind": "matrix_conversation", "room_id": str(room.pk)}


def _mapping(room_id):
    if not valid_id(room_id, "!"):
        return None
    mapping = MatrixRoomMapping.objects.select_related("chat_room__couple", "chat_room__user_one",
        "chat_room__user_two").filter(matrix_room_id=room_id, state="active", chat_room__isnull=False,
            replacement_pending=False, pending_remote_revocations=[]).first()
    if mapping is None:
        return None
    users = [mapping.chat_room.user_one_id, mapping.chat_room.user_two_id]
    if None in users or mapping.member_matrix_localparts != [matrix_localpart(user) for user in sorted(users)]:
        return None
    return mapping


def _member(mapping, mxid):
    if not valid_id(mxid, "@"):
        return None
    room = mapping.chat_room
    identity = MatrixUserMapping.objects.filter(matrix_user_id=mxid, state="active",
        user_id__in=[room.user_one_id, room.user_two_id],
        matrix_localpart__in=mapping.member_matrix_localparts).first()
    if identity is None or not authorized_rooms(identity.user).filter(pk=room.pk).exists():
        return None
    return identity.user


def _encrypted_event(item):
    if item.get("type") != "m.room.encrypted" or "state_key" in item:
        return
    content = item.get("content")
    if not isinstance(content, dict) or content.get("algorithm") != "m.megolm.v1.aes-sha2":
        return
    # Matrix exposes relation metadata outside encryption. Edits and reactions
    # are not new-message alerts; encrypted content is neither decoded nor stored.
    relation = content.get("m.relates_to", {})
    if isinstance(relation, dict) and relation.get("rel_type") in {"m.replace", "m.annotation"}:
        return
    if not isinstance(content.get("ciphertext"), str) or not content["ciphertext"]:
        return
    if isinstance(item.get("unsigned"), dict) and "redacted_because" in item["unsigned"]:
        return
    timestamp = item.get("origin_server_ts")
    now = timezone.now()
    if (type(timestamp) is not int or timestamp < settings.MATRIX_CHAT_PUSH_NOT_BEFORE_MS or
            timestamp <= int((now - MAX_AGE).timestamp() * 1000) or
            timestamp > int((now + timedelta(seconds=30)).timestamp() * 1000)):
        return
    if not valid_id(item.get("event_id"), "$"):
        return
    mapping = _mapping(item.get("room_id"))
    if mapping is None:
        return
    sender = _member(mapping, item.get("sender"))
    if sender is None:
        return
    room = mapping.chat_room
    recipient = room.user_two if sender.pk == room.user_one_id else room.user_one
    _, members = current_couple(sender, room.couple_id)
    event, created = Event.objects.get_or_create(recipient=recipient, kind="matrix.message",
        source=digest(item["room_id"] + "\n" + item["event_id"]), defaults={
            "resource": str(room.pk), "resource_version": mapping.membership_epoch,
            "couple": room.couple, "audience_membership_ids": [member.pk for member in members],
            "matrix_room_id": item["room_id"], "matrix_event_id": item["event_id"],
            "matrix_timestamp_ms": timestamp,
            "expires_at": datetime.fromtimestamp(timestamp / 1000, datetime_timezone.utc) + MAX_AGE})
    if not created or not enabled_for(event) or suppressed(event, read=True):
        return
    # Opt-in compatibility: old clients and Apple installations cannot receive a
    # destination they do not implement. Upgrading never backfills prior events.
    installs = Installation.objects.filter(user=recipient, active=True, permitted=True, platform="android",
        updated_at__gte=installation_cutoff(), session__revoked_at__isnull=True,
        session__expires_at__gt=now)
    for install in installs:
        if CAPABILITY in install.capabilities:
            Delivery.objects.create(event=event, installation=install, binding_version=install.version,
                next_attempt_at=now + timedelta(seconds=2))


def _redaction(item):
    if item.get("type") != "m.room.redaction" or "state_key" in item:
        return
    content = item.get("content", {})
    target = content.get("redacts") if isinstance(content, dict) else None
    target = target or item.get("redacts")
    mapping = _mapping(item.get("room_id"))
    if mapping is None or not valid_id(target, "$"):
        return
    # Synapse has already authorized the redaction. The provisioner may redact
    # too; no message content or actor identity is retained here.
    MatrixPushSuppression.objects.get_or_create(pk=suppression_key(item["room_id"], target))
    Delivery.objects.filter(event__kind="matrix.message", event__matrix_room_id=item["room_id"],
        event__matrix_event_id=target, state__in=["pending", "sending"]).update(state="suppressed", outcome="redacted")


def _receipt(item):
    if item.get("type") != "m.receipt" or not isinstance(item.get("content"), dict):
        return
    mapping = _mapping(item.get("room_id"))
    if mapping is None:
        return
    for event_id, receipts in item["content"].items():
        if not valid_id(event_id, "$") or not isinstance(receipts, dict):
            continue
        for kind in ("m.read", "m.read.private"):
            readers = receipts.get(kind, {})
            if not isinstance(readers, dict):
                continue
            for mxid, details in readers.items():
                # A threaded receipt must not suppress an unrelated main timeline.
                if not isinstance(details, dict) or details.get("thread_id", "main") != "main":
                    continue
                reader = _member(mapping, mxid)
                if reader is None:
                    continue
                MatrixPushSuppression.objects.get_or_create(pk=suppression_key(item["room_id"], event_id, reader.pk))
                target = Event.objects.filter(kind="matrix.message", matrix_room_id=item["room_id"],
                    matrix_event_id=event_id).first()
                events = Event.objects.filter(kind="matrix.message", matrix_room_id=item["room_id"], recipient=reader)
                events = events.filter(matrix_timestamp_ms__lte=target.matrix_timestamp_ms) if target else events.filter(matrix_event_id=event_id)
                Delivery.objects.filter(event__in=events, state__in=["pending", "sending"]).update(state="suppressed", outcome="read")


@transaction.atomic
def ingest(transaction_id, payload):
    _, created = MatrixPushTransaction.objects.get_or_create(pk=digest(transaction_id))
    if not created or not chat_enabled():
        return
    # Serializing only the affected mappings fences message/receipt races. Lock
    # in stable order for concurrent batches involving multiple relationships.
    all_events = payload["events"] + payload.get("ephemeral", [])
    rooms = {item.get("room_id") for item in all_events if isinstance(item, dict) and valid_id(item.get("room_id"), "!")}
    list(MatrixRoomMapping.objects.select_for_update().filter(matrix_room_id__in=rooms).order_by("pk"))
    for item in payload["events"]:
        if isinstance(item, dict):
            _redaction(item)
    for item in payload["events"]:
        if isinstance(item, dict):
            _encrypted_event(item)
    for item in payload.get("ephemeral", []):
        if isinstance(item, dict):
            _receipt(item)
