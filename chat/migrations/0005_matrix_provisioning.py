import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0004_chat_v2_foundation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MatrixUserMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("matrix_localpart", models.CharField(editable=False, max_length=64, unique=True)),
                ("matrix_user_id", models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True)),
                ("state", models.CharField(choices=[("pending", "Pending"), ("active", "Active"), ("failed", "Failed")], default="pending", max_length=12)),
                ("last_error_code", models.CharField(blank=True, editable=False, max_length=40)),
                ("provisioned_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="matrix_identity", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MatrixRoomMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("membership_epoch", models.PositiveBigIntegerField(default=1, editable=False)),
                ("room_alias_localpart", models.CharField(editable=False, max_length=128, unique=True)),
                ("matrix_room_alias", models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True)),
                ("matrix_room_id", models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True)),
                ("state", models.CharField(choices=[("pending", "Pending"), ("provisioning", "Provisioning"), ("active", "Active"), ("revoke_pending", "Revoke pending"), ("revoking", "Revoking"), ("revoked", "Revoked"), ("failed", "Failed")], default="pending", max_length=20)),
                ("attempts", models.PositiveIntegerField(default=0, editable=False)),
                ("available_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("locked_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("last_error_code", models.CharField(blank=True, editable=False, max_length=40)),
                ("provisioned_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chat_room", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="matrix_mapping", to="chat.chatroom")),
            ],
            options={
                "indexes": [models.Index(fields=["state", "available_at", "id"], name="matrix_room_retry_idx")],
            },
        ),
    ]
