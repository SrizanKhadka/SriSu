"""Irreversibly remove the retired Django chat transport and all of its rows."""

from datetime import date, datetime
import hashlib
import hmac
import json
import os
from uuid import UUID

from django.conf import settings
from django.db import migrations
from django.utils import timezone


LEGACY_MODELS = (
    "ChatOutbox",
    "ChatReceipt",
    "ChatOperation",
    "MessageVisibility",
    "MessageDeletion",
    "MessageReaction",
    "EncryptedAttachment",
    "ChatChange",
    "MessageModel",
    "MediaModel",
)
DESTRUCTION_CONFIRMATION = "DESTROY_LEGACY_CHAT_TRANSPORT_V1"
ATTESTATION_SCOPE = "matrix-cutover-v1"
ROOM_POLICY_VERSION = "srisu-room-policy-v1"
MAX_ATTESTATION_TTL_SECONDS = 900


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _digest(value):
    encoded = json.dumps(
        value,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _configuration_digest():
    secret = settings.MATRIX_JWT_SECRET or ""
    return _digest({
        "policy_version": ROOM_POLICY_VERSION,
        "enabled": bool(settings.MATRIX_ENABLED),
        "service_class": settings.MATRIX_SERVICE_CLASS,
        "public_url": settings.MATRIX_HOMESERVER_URL,
        "internal_url": settings.MATRIX_INTERNAL_HOMESERVER_URL,
        "server_name": settings.MATRIX_SERVER_NAME,
        "protocol_id": settings.MATRIX_PROTOCOL_ID,
        "provisioning_localpart": settings.MATRIX_PROVISIONING_LOCALPART,
        "room_version": settings.MATRIX_ROOM_VERSION,
        "jwt_algorithm": settings.MATRIX_JWT_ALGORITHM,
        "jwt_issuer": settings.MATRIX_JWT_ISSUER,
        "jwt_audience": settings.MATRIX_JWT_AUDIENCE,
        "jwt_secret_digest": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "session_ttl": settings.MATRIX_SESSION_TTL_SECONDS,
        "access_ttl": settings.MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS,
    })


def _rows(model, fields, **filters):
    queryset = model._base_manager.filter(**filters).order_by("pk")
    return [list(row) for row in queryset.values_list(*fields)]


def _state_payload(apps):
    ChatRoom = apps.get_model("chat", "ChatRoom")
    MatrixRoomMapping = apps.get_model("chat", "MatrixRoomMapping")
    MatrixUserMapping = apps.get_model("chat", "MatrixUserMapping")
    UserModel = apps.get_model("authentication", "UserModel")
    CoupleModel = apps.get_model("social", "CoupleModel")
    CoupleConnectionModel = apps.get_model("social", "CoupleConnectionModel")
    CoupleMembershipModel = apps.get_model("social", "CoupleMembershipModel")
    SingleConnectionModel = apps.get_model("social", "SingleConnectionModel")

    rooms = _rows(
        ChatRoom,
        ("id", "user_one_id", "user_two_id", "couple_id", "updated_at"),
    )
    mappings = _rows(
        MatrixRoomMapping,
        (
            "id",
            "chat_room_id",
            "member_matrix_localparts",
            "pending_remote_revocations",
            "replacement_pending",
            "membership_epoch",
            "room_alias_localpart",
            "matrix_room_alias",
            "matrix_room_id",
            "state",
            "attempts",
            "available_at",
            "locked_at",
            "last_error_code",
            "provisioned_at",
            "revoked_at",
            "remote_verified_at",
            "updated_at",
        ),
    )
    identities = _rows(
        MatrixUserMapping,
        (
            "id",
            "user_id",
            "matrix_localpart",
            "matrix_user_id",
            "state",
            "last_error_code",
            "provisioned_at",
            "updated_at",
        ),
    )
    all_couples = _rows(
        CoupleModel,
        ("id", "couple_connection_id", "revision", "updated_at"),
    )
    couple_ids = {row[0] for row in all_couples}
    memberships = _rows(
        CoupleMembershipModel,
        ("id", "couple_id", "user_id", "position", "ended_at"),
        couple_id__in=couple_ids,
    )
    user_ids = {
        user_id
        for row in rooms
        for user_id in (row[1], row[2])
        if user_id is not None
    } | {row[1] for row in identities if row[1] is not None} | {
        row[2] for row in memberships if row[2] is not None
    }
    connection_ids = {row[1] for row in all_couples if row[1] is not None}
    return {
        "rooms": rooms,
        "mappings": mappings,
        "identities": identities,
        "users": _rows(
            UserModel,
            ("id", "phone_number", "is_active", "updated_date"),
            id__in=user_ids,
        ),
        "couples": all_couples,
        "connections": _rows(
            CoupleConnectionModel,
            (
                "id",
                "sender_number",
                "receiver_number",
                "connection_status",
                "ended_at",
                "revision",
                "updated_at",
            ),
            id__in=connection_ids,
        ),
        "memberships": memberships,
        "blocked_edges": _rows(
            SingleConnectionModel,
            (
                "id",
                "sender_number",
                "receiver_number",
                "connection_status",
                "updated_at",
            ),
            connection_status="BLOCKED",
        ),
    }


def _state_digest(apps):
    return _digest(_state_payload(apps))


def _assert_complete_local_mapping_set(payload, attestation):
    users = {row[0]: row for row in payload["users"]}
    couples = {row[0]: row for row in payload["couples"]}
    connections = {row[0]: row for row in payload["connections"]}
    active_memberships = {}
    for row in payload["memberships"]:
        if row[4] is None:
            active_memberships.setdefault(row[1], []).append(row[2])
    blocked_pairs = {
        frozenset((row[1], row[2])) for row in payload["blocked_edges"]
    }
    eligible_couples = {}
    for couple_id, couple in couples.items():
        connection = connections.get(couple[1])
        member_ids = sorted(active_memberships.get(couple_id, []))
        member_users = [users.get(user_id) for user_id in member_ids]
        if (
            connection
            and connection[3] == "ACCEPTED"
            and connection[4] is None
            and len(member_ids) == 2
            and all(user and user[2] is True for user in member_users)
            and frozenset(user[1] for user in member_users) not in blocked_pairs
        ):
            eligible_couples[couple_id] = tuple(member_ids)

    identities = {row[1]: row for row in payload["identities"]}
    required_user_ids = {
        user_id
        for member_ids in eligible_couples.values()
        for user_id in member_ids
    }
    identity_missing = 0
    for user_id in required_user_ids:
        identity = identities.get(user_id)
        if (
            identity is None
            or identity[2] != f"srisu_user_{user_id}"
            or identity[3]
            != f"@srisu_user_{user_id}:{settings.MATRIX_SERVER_NAME}"
            or identity[4] != "active"
        ):
            identity_missing += 1

    rooms_by_couple = {}
    for room in payload["rooms"]:
        rooms_by_couple.setdefault(room[3], []).append(room)
    eligible_room_ids = set()
    room_members = {}
    for couple_id, member_ids in eligible_couples.items():
        rooms = rooms_by_couple.get(couple_id, [])
        if len(rooms) == 1 and tuple(sorted(rooms[0][1:3])) == member_ids:
            eligible_room_ids.add(rooms[0][0])
            room_members[rooms[0][0]] = member_ids

    ready_active = 0
    ineligible_active = 0
    outstanding = 0
    mapped_eligible_ids = set()
    for mapping in payload["mappings"]:
        chat_room_id = mapping[1]
        member_localparts = mapping[2]
        pending_remote_revocations = mapping[3]
        replacement_pending = mapping[4]
        matrix_room_alias = mapping[7]
        matrix_room_id = mapping[8]
        state = mapping[9]
        if (
            state not in {"active", "revoked"}
            or pending_remote_revocations
            or replacement_pending
        ):
            outstanding += 1
        if state == "active":
            if chat_room_id not in eligible_room_ids:
                ineligible_active += 1
            elif (
                matrix_room_alias
                and matrix_room_id
                and not pending_remote_revocations
                and not replacement_pending
                and member_localparts
                == [f"srisu_user_{user_id}" for user_id in room_members[chat_room_id]]
            ):
                ready_active += 1
                mapped_eligible_ids.add(chat_room_id)
    if (
        outstanding
        or identity_missing
        or ineligible_active
        or len(eligible_room_ids) != len(eligible_couples)
        or mapped_eligible_ids != eligible_room_ids
        or attestation.eligible_room_count != len(eligible_couples)
        or attestation.active_room_count != ready_active
        or ready_active != len(eligible_couples)
    ):
        raise RuntimeError(
            "Refusing legacy chat destruction with incomplete Matrix mappings."
        )


def verify_cutover_attestation(apps):
    attestation = (
        apps.get_model("chat", "MatrixCutoverAttestation")
        ._base_manager.filter(scope=ATTESTATION_SCOPE)
        .first()
    )
    now = timezone.now()
    if attestation is None:
        raise RuntimeError("Refusing legacy chat destruction without a readiness attestation.")
    if attestation.verified_at > now or attestation.expires_at <= now:
        raise RuntimeError("Refusing legacy chat destruction with an expired readiness attestation.")
    if (
        attestation.expires_at - attestation.verified_at
    ).total_seconds() > MAX_ATTESTATION_TTL_SECONDS:
        raise RuntimeError("Refusing legacy chat destruction with an invalid attestation lifetime.")
    if not hmac.compare_digest(
        attestation.configuration_digest,
        _configuration_digest(),
    ):
        raise RuntimeError("Refusing legacy chat destruction after Matrix configuration changed.")
    payload = _state_payload(apps)
    if not hmac.compare_digest(attestation.state_digest, _digest(payload)):
        raise RuntimeError("Refusing legacy chat destruction after relationship state changed.")
    _assert_complete_local_mapping_set(payload, attestation)


def destroy_legacy_rows(apps, schema_editor):
    if os.environ.get("SRISU_CONFIRM_DESTROY_LEGACY_CHAT") != DESTRUCTION_CONFIRMATION:
        raise RuntimeError(
            "Refusing irreversible legacy chat destruction. Set "
            f"SRISU_CONFIRM_DESTROY_LEGACY_CHAT={DESTRUCTION_CONFIRMATION} "
            "for the reviewed cutover migration."
        )
    # This proof was written only after live Synapse verification completed.
    # Recompute its local bindings here; migrations perform no remote I/O.
    verify_cutover_attestation(apps)
    # The product owner explicitly chose a clean Matrix-only cutover. This is
    # intentionally irreversible: neither plaintext nor opaque-envelope rows
    # are copied into Matrix, and reverse migration must not imply recovery.
    for model_name in LEGACY_MODELS:
        apps.get_model("chat", model_name).objects.all().delete()
    # Personal/dating chat was retired before Matrix. Keep the social
    # SingleConnectionModel rows (including BLOCKED privacy policy), but remove
    # their obsolete transport identities before dropping the relationship FK.
    apps.get_model("chat", "ChatRoom").objects.filter(couple__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0005a_durable_matrix_revocation"),
    ]

    operations = [
        migrations.RunPython(destroy_legacy_rows, reverse_code=None),
        migrations.RemoveField(model_name="chatroom", name="last_message"),
        migrations.RemoveField(model_name="chatroom", name="pinned_messages"),
        migrations.RemoveField(model_name="chatroom", name="encrypted_v2_started_at"),
        migrations.RemoveField(model_name="chatroom", name="is_typing"),
        migrations.RemoveField(model_name="chatroom", name="last_sequence"),
        migrations.RemoveField(model_name="chatroom", name="unread_count"),
        migrations.RemoveIndex(
            model_name="chatroom",
            name="chat_chatro_chat_ty_037291_idx",
        ),
        migrations.RemoveField(model_name="chatroom", name="chat_type"),
        migrations.RemoveField(model_name="chatroom", name="singles"),
        migrations.RemoveField(model_name="chatroom", name="settings"),
        migrations.DeleteModel(name="ChatOutbox"),
        migrations.DeleteModel(name="ChatReceipt"),
        migrations.DeleteModel(name="ChatOperation"),
        migrations.DeleteModel(name="MessageVisibility"),
        migrations.DeleteModel(name="MessageDeletion"),
        migrations.DeleteModel(name="MessageReaction"),
        migrations.DeleteModel(name="EncryptedAttachment"),
        migrations.DeleteModel(name="ChatChange"),
        migrations.DeleteModel(name="MessageModel"),
        migrations.DeleteModel(name="MediaModel"),
    ]
