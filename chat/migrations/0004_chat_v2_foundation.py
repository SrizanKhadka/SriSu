import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


def require_unique_couple_rooms(apps, schema_editor):
    """Fail safely before the constraint instead of choosing history to detach."""
    ChatRoom = apps.get_model("chat", "ChatRoom")
    duplicates = list(
        ChatRoom.objects.exclude(couple_id=None)
        .values("couple_id")
        .annotate(room_count=Count("id"))
        .filter(room_count__gt=1)
        .order_by("couple_id")
        .values_list("couple_id", "room_count")[:50]
    )
    if duplicates:
        detail = ", ".join(f"{couple_id}:{count}" for couple_id, count in duplicates)
        raise RuntimeError(
            "chat 0004 requires one room per couple; duplicate couple_id:count "
            f"values (first 50) are {detail}. Resolve them explicitly before retrying."
        )


def require_resolved_private_deletions(apps, schema_editor):
    """Do not guess the actor for the legacy global DELETE_FOR_ME flag."""
    Message = apps.get_model("chat", "MessageModel")
    ambiguous_ids = list(
        Message.objects.filter(delete_option="DELETE_FOR_ME")
        .exclude(deletions__delete_option="DELETE_FOR_ME")
        .order_by("id")
        .values_list("id", flat=True)[:50]
    )
    if ambiguous_ids:
        detail = ", ".join(str(message_id) for message_id in ambiguous_ids)
        raise RuntimeError(
            "chat 0004 found legacy DELETE_FOR_ME rows with no per-user actor "
            f"record (first 50 message ids: {detail}). Explicitly reconcile "
            "the deleting participant before retrying."
        )
    # Rows with an explicit per-user record are safe to normalize: the scoped
    # deletion remains authoritative and the partner's shared row is visible.
    Message.objects.filter(
        delete_option="DELETE_FOR_ME",
        deletions__delete_option="DELETE_FOR_ME",
    ).update(delete_option="NOT_DELETED")


class Migration(migrations.Migration):
    dependencies = [
        ("authentication", "0022_device_sessions"),
        ("chat", "0003_couple_profile_cards"),
        ("social", "0014_relationship_lifecycle"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            require_resolved_private_deletions,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="chatroom",
            name="unique_chatroom_users",
        ),
        migrations.AddField(
            model_name="chatroom",
            name="encrypted_v2_started_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="chatroom",
            name="last_sequence",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mediamodel",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="legacy_chat_uploads",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="mediamodel",
            name="chat_room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="legacy_media",
                to="chat.chatroom",
            ),
        ),
        migrations.AddField(
            model_name="mediamodel",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="client_operation_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="content_kind",
            field=models.CharField(default="legacy", max_length=24),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="encrypted_envelope",
            field=models.JSONField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="legacy_plaintext",
            field=models.BooleanField(default=True, editable=False),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="payload_hash",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="sender_device_session",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chat_messages",
                to="authentication.devicesession",
            ),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="sequence",
            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="messagemodel",
            name="tombstoned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="messagereaction",
            name="encrypted_envelope",
            field=models.JSONField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="messagereaction",
            name="is_removed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="messagereaction",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="messagereaction",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="messagereaction",
            name="reaction",
            field=models.CharField(
                blank=True,
                choices=[
                    ("HAHA", "Haha"),
                    ("WOW", "Wow"),
                    ("SAD", "Sad"),
                    ("ANGRY", "Angry"),
                    ("Love", "Love"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ChatOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.UUIDField()),
                ("kind", models.CharField(max_length=32)),
                ("payload_hash", models.CharField(max_length=64)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_operations", to=settings.AUTH_USER_MODEL)),
                ("chat_room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="operations", to="chat.chatroom")),
            ],
            options={
                "indexes": [models.Index(fields=["chat_room", "actor", "-created_at"], name="chat_operation_actor_idx")],
                "constraints": [models.UniqueConstraint(fields=("chat_room", "actor", "operation_id"), name="unique_chat_operation")],
            },
        ),
        migrations.CreateModel(
            name="ChatChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveBigIntegerField()),
                ("kind", models.CharField(max_length=32)),
                ("message_revision", models.PositiveBigIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_changes", to=settings.AUTH_USER_MODEL)),
                ("audience_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="private_chat_changes", to=settings.AUTH_USER_MODEL)),
                ("chat_room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="changes", to="chat.chatroom")),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="changes", to="chat.messagemodel")),
            ],
            options={
                "ordering": ["sequence"],
                "indexes": [models.Index(fields=["chat_room", "sequence"], name="chat_change_sequence_idx")],
                "constraints": [models.UniqueConstraint(fields=("chat_room", "sequence"), name="unique_chat_change_sequence")],
            },
        ),
        migrations.CreateModel(
            name="ChatOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state", models.CharField(choices=[("pending", "Pending"), ("publishing", "Publishing"), ("published", "Published")], default="pending", max_length=12)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("change", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="outbox", to="chat.chatchange")),
            ],
            options={"indexes": [models.Index(fields=["state", "available_at", "id"], name="chat_outbox_pending_idx")]},
        ),
        migrations.CreateModel(
            name="ChatReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("delivered_through", models.PositiveBigIntegerField(default=0)),
                ("read_through", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chat_room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="receipt_cursors", to="chat.chatroom")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_receipt_cursors", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("chat_room", "user"), name="unique_chat_receipt"),
                    models.CheckConstraint(condition=models.Q(read_through__lte=models.F("delivered_through")), name="chat_receipt_read_lte_delivered"),
                ]
            },
        ),
        migrations.CreateModel(
            name="MessageVisibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hidden_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="visibility_records", to="chat.messagemodel")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_message_visibility", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("message", "user"), name="unique_chat_message_visibility")]},
        ),
        migrations.CreateModel(
            name="EncryptedAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation_id", models.UUIDField()),
                ("ciphertext", models.FileField(upload_to="chat_encrypted/")),
                ("ciphertext_sha256", models.CharField(max_length=64)),
                ("ciphertext_size", models.PositiveBigIntegerField()),
                ("state", models.CharField(choices=[("staged", "Staged"), ("claimed", "Claimed"), ("cancelled", "Cancelled")], default="staged", max_length=12)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chat_room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="encrypted_attachments", to="chat.chatroom")),
                ("claimed_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="encrypted_attachments", to="chat.messagemodel")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="encrypted_chat_attachments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["state", "expires_at"], name="chat_attachment_expiry_idx")],
                "constraints": [models.UniqueConstraint(fields=("chat_room", "owner", "operation_id"), name="unique_encrypted_attachment_operation")],
            },
        ),
        migrations.AddIndex(
            model_name="chatroom",
            index=models.Index(fields=["user_one", "user_two"], name="chat_room_pair_idx"),
        ),
        migrations.AddIndex(
            model_name="messagemodel",
            index=models.Index(fields=["chat_room", "-sequence"], name="chat_message_sequence_idx"),
        ),
        migrations.RunPython(
            require_unique_couple_rooms,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="chatroom",
            constraint=models.UniqueConstraint(condition=models.Q(couple__isnull=False), fields=("couple",), name="unique_chatroom_couple"),
        ),
        migrations.AddConstraint(
            model_name="messagemodel",
            constraint=models.UniqueConstraint(condition=models.Q(client_operation_id__isnull=False), fields=("chat_room", "sender", "client_operation_id"), name="unique_chat_message_operation"),
        ),
        migrations.AddConstraint(
            model_name="messagemodel",
            constraint=models.UniqueConstraint(condition=models.Q(sequence__isnull=False), fields=("chat_room", "sequence"), name="unique_chat_message_sequence"),
        ),
    ]
