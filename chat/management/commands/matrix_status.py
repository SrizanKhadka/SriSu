"""Report non-sensitive private-Matrix control-plane readiness."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.db.migrations.recorder import MigrationRecorder

from chat.matrix_readiness import (
    clear_cutover_attestation,
    validate_cutover_attestation,
    write_cutover_attestation,
)
from chat.models import (
    ChatRoom,
    MatrixCutoverAttestation,
    MatrixRoomMapping,
    MatrixUserMapping,
)
from chat.selectors.access import eligible_relationship_members
from chat.services.matrix_provisioning import (
    DisabledMatrixService,
    MatrixServiceError,
    get_matrix_service,
    verify_active_matrix_room,
)


REQUIRED_MIGRATION = ("chat", "0005a_durable_matrix_revocation")
DESTRUCTIVE_MIGRATION = ("chat", "0006_destroy_legacy_chat_transport")
REQUIRED_TABLES = {
    MatrixRoomMapping._meta.db_table,
    MatrixUserMapping._meta.db_table,
    MatrixCutoverAttestation._meta.db_table,
}


def _wire(value):
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _local_readiness_snapshot():
    eligible_members = eligible_relationship_members()
    required_user_ids = {
        user_id
        for member_ids in eligible_members.values()
        for user_id in member_ids
    }
    identities = {
        identity.user_id: identity
        for identity in MatrixUserMapping.objects.filter(user_id__in=required_user_ids)
    }
    identity_missing = 0
    for user_id in required_user_ids:
        identity = identities.get(user_id)
        if (
            identity is None
            or identity.state != MatrixUserMapping.State.ACTIVE
            or identity.matrix_localpart != f"srisu_user_{user_id}"
            or identity.matrix_user_id
            != f"@srisu_user_{user_id}:{settings.MATRIX_SERVER_NAME}"
        ):
            identity_missing += 1
    rooms_by_couple = {}
    for room_id, couple_id, user_one_id, user_two_id in ChatRoom.objects.filter(
        couple_id__in=eligible_members,
    ).values_list("pk", "couple_id", "user_one_id", "user_two_id"):
        rooms_by_couple.setdefault(couple_id, []).append(
            (room_id, tuple(sorted([user_one_id, user_two_id])))
        )
    valid_room_ids = set()
    for couple_id, member_ids in eligible_members.items():
        rooms = rooms_by_couple.get(couple_id, [])
        if len(rooms) == 1 and rooms[0][1] == member_ids:
            valid_room_ids.add(rooms[0][0])

    ready_eligible = (
        MatrixRoomMapping.objects.filter(
            chat_room_id__in=valid_room_ids,
            state=MatrixRoomMapping.State.ACTIVE,
            matrix_room_id__isnull=False,
            matrix_room_alias__isnull=False,
            replacement_pending=False,
            pending_remote_revocations=[],
        )
        .exclude(matrix_room_id="")
        .exclude(matrix_room_alias="")
        .count()
    )
    ineligible_active = MatrixRoomMapping.objects.filter(
        state=MatrixRoomMapping.State.ACTIVE,
    ).exclude(chat_room_id__in=valid_room_ids).count()
    outstanding_work = (
        MatrixRoomMapping.objects.exclude(
            state__in=[
                MatrixRoomMapping.State.ACTIVE,
                MatrixRoomMapping.State.REVOKED,
            ]
        ).count()
        + MatrixRoomMapping.objects.exclude(pending_remote_revocations=[]).count()
    )
    eligible_count = len(eligible_members)
    return {
        "eligible_count": eligible_count,
        "eligible_missing_active": eligible_count - ready_eligible,
        "eligible_identity_missing": identity_missing,
        "ineligible_active": ineligible_active,
        "outstanding_work": outstanding_work,
        "ready_eligible": ready_eligible,
        "valid_room_ids": valid_room_ids,
        "relationship_rooms_ready": (
            ready_eligible == eligible_count
            and identity_missing == 0
            and ineligible_active == 0
            and outstanding_work == 0
        ),
    }


class Command(BaseCommand):
    help = "Print safe Matrix configuration, schema, and retry-state diagnostics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require-ready",
            action="store_true",
            help=(
                "Exit nonzero unless configuration/schema are ready and every "
                "currently eligible relationship has exactly one ACTIVE room."
            ),
        )

    def handle(self, *args, **options):
        require_ready = options["require_ready"]
        try:
            tables = set(connection.introspection.table_names())
        except DatabaseError:
            tables = set()
        schema_ready = REQUIRED_TABLES.issubset(tables)
        migration_applied = False
        destructive_migration_applied = False
        if MigrationRecorder.Migration._meta.db_table in tables:
            try:
                migration_applied = MigrationRecorder.Migration.objects.filter(
                    app=REQUIRED_MIGRATION[0],
                    name=REQUIRED_MIGRATION[1],
                ).exists()
                destructive_migration_applied = MigrationRecorder.Migration.objects.filter(
                    app=DESTRUCTIVE_MIGRATION[0],
                    name=DESTRUCTIVE_MIGRATION[1],
                ).exists()
            except DatabaseError:
                pass

        matrix_service = DisabledMatrixService()
        try:
            matrix_service = get_matrix_service()
            configuration_ready = not isinstance(
                matrix_service, DisabledMatrixService
            )
        except MatrixServiceError:
            configuration_ready = False

        counts = {state: "unavailable" for state, _ in MatrixRoomMapping.State.choices}
        identity_count = "unavailable"
        eligible_count = "unavailable"
        eligible_missing_active = "unavailable"
        eligible_identity_missing = "unavailable"
        ineligible_active = "unavailable"
        outstanding_work = "unavailable"
        relationship_rooms_ready = False
        live_verified = 0
        policy_verified = False
        live_verification_failures = "not_run"
        attestation_valid = False
        attestation_reason = "unavailable"
        attestation_expires_at = "unavailable"
        if require_ready and schema_ready and migration_applied:
            # A failed/retried readiness run must never leave an older proof
            # available for the destructive migration.
            clear_cutover_attestation()
        if schema_ready and migration_applied:
            try:
                identity_count = MatrixUserMapping.objects.count()
                for state in counts:
                    counts[state] = MatrixRoomMapping.objects.filter(state=state).count()
                snapshot = _local_readiness_snapshot()
                eligible_count = snapshot["eligible_count"]
                eligible_missing_active = snapshot["eligible_missing_active"]
                eligible_identity_missing = snapshot["eligible_identity_missing"]
                ineligible_active = snapshot["ineligible_active"]
                outstanding_work = snapshot["outstanding_work"]
                relationship_rooms_ready = snapshot["relationship_rooms_ready"]
            except DatabaseError:
                pass

        local_ready = (
            configuration_ready
            and schema_ready
            and migration_applied
            and relationship_rooms_ready
        )
        if require_ready and local_ready:
            live_verification_failures = 0
            try:
                matrix_service.verify_policy()
                policy_verified = True
            except MatrixServiceError:
                live_verification_failures += 1
            mappings = list(
                MatrixRoomMapping.objects.filter(
                    chat_room_id__in=snapshot["valid_room_ids"],
                    state=MatrixRoomMapping.State.ACTIVE,
                ).order_by("id")
            )
            if policy_verified:
                for mapping in mappings:
                    if verify_active_matrix_room(
                        mapping.pk,
                        service=matrix_service,
                        recover=True,
                    ):
                        live_verified += 1
                    else:
                        live_verification_failures += 1

            # Verification may durably queue or perform a replacement. Re-read
            # the complete local state and attest only an unchanged, exact set.
            snapshot = _local_readiness_snapshot()
            eligible_count = snapshot["eligible_count"]
            ready_eligible = snapshot["ready_eligible"]
            eligible_missing_active = snapshot["eligible_missing_active"]
            eligible_identity_missing = snapshot["eligible_identity_missing"]
            ineligible_active = snapshot["ineligible_active"]
            outstanding_work = snapshot["outstanding_work"]
            relationship_rooms_ready = snapshot["relationship_rooms_ready"]
            if (
                relationship_rooms_ready
                and policy_verified
                and live_verification_failures == 0
                and live_verified == eligible_count
            ):
                try:
                    write_cutover_attestation(
                        eligible_room_count=eligible_count,
                        active_room_count=ready_eligible,
                    )
                except ValueError as exc:
                    raise CommandError(str(exc)) from None

        if schema_ready and migration_applied:
            attestation_valid, attestation_reason = validate_cutover_attestation()
            attestation = MatrixCutoverAttestation.objects.filter(
                scope="matrix-cutover-v1"
            ).first()
            if attestation is not None:
                attestation_expires_at = attestation.expires_at.isoformat()

        cutover_ready = local_ready and attestation_valid

        secret = settings.MATRIX_JWT_SECRET
        values = {
            "matrix_enabled": settings.MATRIX_ENABLED,
            "matrix_configuration_ready": configuration_ready,
            "matrix_schema_ready": schema_ready,
            "matrix_required_migration_applied": migration_applied,
            "matrix_destructive_migration_applied": destructive_migration_applied,
            "matrix_service_class": settings.MATRIX_SERVICE_CLASS,
            "matrix_public_url_configured": bool(settings.MATRIX_HOMESERVER_URL),
            "matrix_internal_url_configured": bool(settings.MATRIX_INTERNAL_HOMESERVER_URL),
            "matrix_server_name_configured": bool(settings.MATRIX_SERVER_NAME),
            "matrix_provisioning_identity_configured": bool(
                settings.MATRIX_PROVISIONING_LOCALPART
            ),
            "matrix_room_version": settings.MATRIX_ROOM_VERSION,
            "matrix_access_token_lifetime_seconds": (
                settings.MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS
            ),
            "matrix_jwt_secret_configured": bool(secret and not secret.startswith("replace-")),
            "matrix_identity_mappings": identity_count,
            "matrix_eligible_relationship_rooms": eligible_count,
            "matrix_eligible_missing_active": eligible_missing_active,
            "matrix_eligible_identity_missing": eligible_identity_missing,
            "matrix_ineligible_active": ineligible_active,
            "matrix_outstanding_work": outstanding_work,
            "matrix_live_verified": live_verified,
            "matrix_policy_verified": policy_verified,
            "matrix_live_verification_failures": live_verification_failures,
            "matrix_attestation_valid": attestation_valid,
            "matrix_attestation_reason": attestation_reason,
            "matrix_attestation_expires_at": attestation_expires_at,
            "matrix_cutover_ready": cutover_ready,
        }
        values.update({f"matrix_rooms_{state}": count for state, count in counts.items()})
        for key, value in values.items():
            self.stdout.write(f"{key}={_wire(value)}")
        if require_ready and not cutover_ready:
            raise CommandError(
                "Matrix cutover is not ready; inspect the non-sensitive status "
                "fields above before irreversible migration 0006."
            )
