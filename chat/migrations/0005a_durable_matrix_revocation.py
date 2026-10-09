import django.db.models.deletion
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


ATTESTATION_SCOPE = "matrix-cutover-v1"
ROOM_POLICY_VERSION = "srisu-room-policy-v1"
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


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


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


def seed_empty_database_attestation(apps, schema_editor):
    ChatRoom = apps.get_model("chat", "ChatRoom")
    MatrixRoomMapping = apps.get_model("chat", "MatrixRoomMapping")
    MatrixUserMapping = apps.get_model("chat", "MatrixUserMapping")
    if (
        ChatRoom.objects.exists()
        or MatrixRoomMapping.objects.exists()
        or MatrixUserMapping.objects.exists()
        or apps.get_model("social", "CoupleModel").objects.exists()
        or any(apps.get_model("chat", name).objects.exists() for name in LEGACY_MODELS)
        or apps.get_model("social", "SingleConnectionModel").objects.filter(
            connection_status="BLOCKED"
        ).exists()
    ):
        return
    ttl = settings.MATRIX_CUTOVER_ATTESTATION_TTL_SECONDS
    if not 30 <= ttl <= 900:
        raise RuntimeError(
            "MATRIX_CUTOVER_ATTESTATION_TTL_SECONDS must be between 30 and 900"
        )
    now = timezone.now()
    empty_state = {
        "rooms": [],
        "mappings": [],
        "identities": [],
        "users": [],
        "couples": [],
        "connections": [],
        "memberships": [],
        "blocked_edges": [],
    }
    apps.get_model("chat", "MatrixCutoverAttestation").objects.create(
        scope=ATTESTATION_SCOPE,
        configuration_digest=_configuration_digest(),
        state_digest=_digest(empty_state),
        eligible_room_count=0,
        active_room_count=0,
        verified_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )


def snapshot_member_localparts(apps, schema_editor):
    MatrixRoomMapping = apps.get_model("chat", "MatrixRoomMapping")
    for mapping in MatrixRoomMapping.objects.select_related("chat_room").iterator():
        room = mapping.chat_room
        mapping.member_matrix_localparts = [
            f"srisu_user_{user_id}"
            for user_id in sorted(
                user_id
                for user_id in [room.user_one_id, room.user_two_id]
                if user_id is not None
            )
        ]
        mapping.save(update_fields=["member_matrix_localparts"])


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0005_matrix_provisioning"),
    ]

    operations = [
        migrations.AddField(
            model_name="matrixroommapping",
            name="member_matrix_localparts",
            field=models.JSONField(default=list, editable=False),
        ),
        migrations.AddField(
            model_name="matrixroommapping",
            name="pending_remote_revocations",
            field=models.JSONField(default=list, editable=False),
        ),
        migrations.AddField(
            model_name="matrixroommapping",
            name="replacement_pending",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="matrixroommapping",
            name="remote_verified_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.CreateModel(
            name="MatrixCutoverAttestation",
            fields=[
                ("scope", models.CharField(editable=False, max_length=40, primary_key=True, serialize=False)),
                ("configuration_digest", models.CharField(editable=False, max_length=64)),
                ("state_digest", models.CharField(editable=False, max_length=64)),
                ("eligible_room_count", models.PositiveIntegerField(editable=False)),
                ("active_room_count", models.PositiveIntegerField(editable=False)),
                ("verified_at", models.DateTimeField(editable=False)),
                ("expires_at", models.DateTimeField(editable=False)),
            ],
            options={"verbose_name": "Matrix cutover attestation"},
        ),
        migrations.RunPython(snapshot_member_localparts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="matrixroommapping",
            name="chat_room",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="matrix_mapping",
                to="chat.chatroom",
            ),
        ),
        migrations.RunPython(
            seed_empty_database_attestation,
            migrations.RunPython.noop,
        ),
    ]
