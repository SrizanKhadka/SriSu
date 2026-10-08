"""Report non-sensitive chat-v2 rollout and worker readiness diagnostics."""

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.recorder import MigrationRecorder
from django.core.management.base import BaseCommand

from authentication.models import DeviceSession
from chat.models import ChatChange, ChatOutbox, ChatRoom, MessageModel
from social.models import CoupleMembershipModel


REQUIRED_MIGRATIONS = {
    ("authentication", "0022_device_sessions"),
    ("social", "0014_relationship_lifecycle"),
    ("chat", "0004_chat_v2_foundation"),
}
REQUIRED_TABLES = {
    model._meta.db_table
    for model in (
        DeviceSession,
        CoupleMembershipModel,
        ChatRoom,
        MessageModel,
        ChatChange,
        ChatOutbox,
    )
}


def _wire(value):
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


class Command(BaseCommand):
    help = "Print safe chat-v2 configuration, schema, backlog, and outbox status."

    def handle(self, *args, **options):
        try:
            tables = set(connection.introspection.table_names())
        except DatabaseError:
            tables = set()

        schema_ready = REQUIRED_TABLES.issubset(tables)
        migrations_applied = False
        if MigrationRecorder.Migration._meta.db_table in tables:
            try:
                applied = set(
                    MigrationRecorder.Migration.objects.filter(
                        app__in={app for app, _ in REQUIRED_MIGRATIONS}
                    ).values_list("app", "name")
                )
                migrations_applied = REQUIRED_MIGRATIONS.issubset(applied)
            except DatabaseError:
                migrations_applied = False

        unsequenced = pending = retrying = "unavailable"
        if schema_ready:
            try:
                unsequenced = MessageModel.objects.filter(sequence__isnull=True).count()
                pending = ChatOutbox.objects.filter(
                    state=ChatOutbox.State.PENDING
                ).count()
                retrying = ChatOutbox.objects.filter(
                    state=ChatOutbox.State.PENDING,
                ).exclude(last_error_code="").count()
            except DatabaseError:
                pass

        values = {
            "chat_v2_schema_ready": schema_ready,
            "chat_v2_required_migrations_applied": migrations_applied,
            "protocol_status": settings.CHAT_V2_PROTOCOL_STATUS,
            "encrypted_writes_enabled": settings.CHAT_V2_ENCRYPTED_WRITES_ENABLED,
            "test_adapter_enabled": settings.CHAT_V2_TEST_ADAPTER_ENABLED,
            "allowlisted_user_count": len(settings.CHAT_V2_ALLOWED_USER_IDS),
            "requires_device_session": settings.CHAT_V2_REQUIRE_DEVICE_SESSION,
            "attachment_staging_enabled": settings.CHAT_V2_ATTACHMENT_STAGING_ENABLED,
            "legacy_unsequenced_messages": unsequenced,
            "outbox_pending": pending,
            "outbox_retrying": retrying,
        }
        for key, value in values.items():
            self.stdout.write(f"{key}={_wire(value)}")
