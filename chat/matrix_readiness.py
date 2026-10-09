"""Deterministic, non-secret Matrix cutover readiness fingerprints."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
from uuid import UUID

from django.apps import apps as django_apps
from django.conf import settings
from django.utils import timezone


ATTESTATION_SCOPE = "matrix-cutover-v1"
ROOM_POLICY_VERSION = "srisu-room-policy-v1"
MAX_ATTESTATION_TTL_SECONDS = 900


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _digest(value) -> str:
    encoded = json.dumps(
        value,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def matrix_configuration_digest() -> str:
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


def matrix_state_payload(registry=django_apps):
    ChatRoom = registry.get_model("chat", "ChatRoom")
    MatrixRoomMapping = registry.get_model("chat", "MatrixRoomMapping")
    MatrixUserMapping = registry.get_model("chat", "MatrixUserMapping")
    UserModel = registry.get_model("authentication", "UserModel")
    CoupleModel = registry.get_model("social", "CoupleModel")
    CoupleConnectionModel = registry.get_model("social", "CoupleConnectionModel")
    CoupleMembershipModel = registry.get_model("social", "CoupleMembershipModel")
    SingleConnectionModel = registry.get_model("social", "SingleConnectionModel")

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
        # BLOCKED is a surviving privacy policy. Include every blocked edge so
        # an edge committed after verification always invalidates the proof.
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


def matrix_state_digest(registry=django_apps) -> str:
    return _digest(matrix_state_payload(registry))


def write_cutover_attestation(*, eligible_room_count: int, active_room_count: int):
    MatrixCutoverAttestation = django_apps.get_model(
        "chat", "MatrixCutoverAttestation"
    )
    ttl = settings.MATRIX_CUTOVER_ATTESTATION_TTL_SECONDS
    if not 30 <= ttl <= MAX_ATTESTATION_TTL_SECONDS:
        raise ValueError("Matrix cutover attestation TTL must be between 30 and 900 seconds")
    now = timezone.now()
    attestation, _ = MatrixCutoverAttestation.objects.update_or_create(
        scope=ATTESTATION_SCOPE,
        defaults={
            "configuration_digest": matrix_configuration_digest(),
            "state_digest": matrix_state_digest(),
            "eligible_room_count": eligible_room_count,
            "active_room_count": active_room_count,
            "verified_at": now,
            "expires_at": now + timedelta(seconds=ttl),
        },
    )
    return attestation


def clear_cutover_attestation() -> None:
    MatrixCutoverAttestation = django_apps.get_model(
        "chat", "MatrixCutoverAttestation"
    )
    MatrixCutoverAttestation.objects.filter(scope=ATTESTATION_SCOPE).delete()


def validate_cutover_attestation(*, now=None) -> tuple[bool, str]:
    MatrixCutoverAttestation = django_apps.get_model(
        "chat", "MatrixCutoverAttestation"
    )
    attestation = MatrixCutoverAttestation.objects.filter(
        scope=ATTESTATION_SCOPE
    ).first()
    if attestation is None:
        return False, "missing"
    current = now or timezone.now()
    if attestation.verified_at > current or attestation.expires_at <= current:
        return False, "expired"
    if (
        attestation.expires_at - attestation.verified_at
    ).total_seconds() > MAX_ATTESTATION_TTL_SECONDS:
        return False, "lifetime_invalid"
    if not hmac.compare_digest(
        attestation.configuration_digest,
        matrix_configuration_digest(),
    ):
        return False, "configuration_mismatch"
    if not hmac.compare_digest(attestation.state_digest, matrix_state_digest()):
        return False, "state_mismatch"
    if attestation.eligible_room_count != attestation.active_room_count:
        return False, "count_mismatch"
    return True, "ready"
