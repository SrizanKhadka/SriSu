from datetime import timedelta
from importlib import import_module
import os
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.apps import apps as django_apps
from django.db import migrations
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from chat.admin import MatrixRoomMappingAdmin
from chat.matrix_readiness import write_cutover_attestation
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping


class LegacyTransportRetirementTests(SimpleTestCase):
    def test_additive_revocation_shape_precedes_destructive_cutover(self):
        additive = import_module("chat.migrations.0005a_durable_matrix_revocation")
        destructive = import_module("chat.migrations.0006_destroy_legacy_chat_transport")

        self.assertEqual(
            additive.Migration.dependencies,
            [("chat", "0005_matrix_provisioning")],
        )
        self.assertEqual(
            {
                operation.name
                for operation in additive.Migration.operations
                if isinstance(operation, migrations.AddField)
            },
            {
                "member_matrix_localparts",
                "pending_remote_revocations",
                "replacement_pending",
                "remote_verified_at",
            },
        )
        self.assertTrue(
            any(
                isinstance(operation, migrations.CreateModel)
                and operation.name == "MatrixCutoverAttestation"
                for operation in additive.Migration.operations
            )
        )
        self.assertEqual(
            destructive.Migration.dependencies,
            [("chat", "0005a_durable_matrix_revocation")],
        )

    def test_forward_migration_is_explicitly_irreversible_and_complete(self):
        module = import_module("chat.migrations.0006_destroy_legacy_chat_transport")
        migration = module.Migration
        operations = migration.operations

        purge = operations[0]
        self.assertIsInstance(purge, migrations.RunPython)
        self.assertIsNone(purge.reverse_code)
        self.assertEqual(
            {
                operation.name
                for operation in operations
                if isinstance(operation, migrations.DeleteModel)
            },
            {
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
            },
        )
        self.assertEqual(
            {
                operation.name
                for operation in operations
                if isinstance(operation, migrations.RemoveIndex)
            },
            {"chat_chatro_chat_ty_037291_idx"},
        )
        self.assertEqual(
            {
                operation.name
                for operation in operations
                if isinstance(operation, migrations.RemoveField)
            },
            {
                "last_message",
                "pinned_messages",
                "encrypted_v2_started_at",
                "is_typing",
                "last_sequence",
                "unread_count",
                "chat_type",
                "singles",
                "settings",
            },
        )

    def test_forward_purge_deletes_non_couple_rooms_but_not_relationship_rows(self):
        module = import_module("chat.migrations.0006_destroy_legacy_chat_transport")
        models = {name: Mock() for name in (*module.LEGACY_MODELS, "ChatRoom")}
        apps = Mock()
        apps.get_model.side_effect = lambda app, name: models[name]

        with patch.dict(
            os.environ,
            {"SRISU_CONFIRM_DESTROY_LEGACY_CHAT": module.DESTRUCTION_CONFIRMATION},
        ), patch.object(module, "verify_cutover_attestation"):
            module.destroy_legacy_rows(apps, schema_editor=None)

        for name in module.LEGACY_MODELS:
            models[name].objects.all.return_value.delete.assert_called_once_with()
        models["ChatRoom"].objects.filter.assert_called_once_with(couple__isnull=True)
        models["ChatRoom"].objects.filter.return_value.delete.assert_called_once_with()

    def test_forward_purge_requires_exact_operator_confirmation_before_db_access(self):
        module = import_module("chat.migrations.0006_destroy_legacy_chat_transport")
        apps = Mock()
        for value in (None, "yes", "DESTROY_LEGACY_CHAT_TRANSPORT"):
            environment = {}
            if value is not None:
                environment["SRISU_CONFIRM_DESTROY_LEGACY_CHAT"] = value
            with self.subTest(value=value), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "Refusing irreversible"):
                    module.destroy_legacy_rows(apps, schema_editor=None)
        apps.get_model.assert_not_called()

    def test_relationship_identity_and_matrix_control_plane_remain(self):
        fields = {field.name: field for field in ChatRoom._meta.get_fields()}
        self.assertEqual(fields["id"].get_internal_type(), "UUIDField")
        self.assertTrue({"user_one", "user_two", "couple", "created_at", "updated_at"} <= fields.keys())
        self.assertFalse(
            {
                "last_message",
                "pinned_messages",
                "encrypted_v2_started_at",
                "is_typing",
                "last_sequence",
                "unread_count",
                "chat_type",
                "singles",
                "settings",
            }
            & fields.keys()
        )
        self.assertEqual(MatrixRoomMapping._meta.get_field("chat_room").related_model, ChatRoom)
        self.assertTrue(MatrixRoomMapping._meta.get_field("chat_room").null)
        self.assertIn("member_matrix_localparts", {
            field.name for field in MatrixRoomMapping._meta.get_fields()
        })
        self.assertIsNotNone(MatrixUserMapping._meta.get_field("user").related_model)

    def test_matrix_revocation_tombstones_are_read_only_and_non_deletable_in_admin(self):
        model_admin = MatrixRoomMappingAdmin(MatrixRoomMapping, AdminSite())
        self.assertFalse(model_admin.has_add_permission(request=None))
        self.assertFalse(model_admin.has_delete_permission(request=None))
        self.assertEqual(
            set(model_admin.readonly_fields),
            {field.name for field in MatrixRoomMapping._meta.fields},
        )


class MatrixCutoverAttestationTests(TestCase):
    def setUp(self):
        django_apps.get_model("chat", "MatrixCutoverAttestation").objects.all().delete()
        self.module = import_module("chat.migrations.0006_destroy_legacy_chat_transport")

    def test_migration_accepts_fresh_attestation_bound_to_empty_state(self):
        write_cutover_attestation(eligible_room_count=0, active_room_count=0)
        self.module.verify_cutover_attestation(django_apps)

    def test_migration_rejects_missing_expired_and_changed_attestation(self):
        with self.assertRaisesRegex(RuntimeError, "without a readiness attestation"):
            self.module.verify_cutover_attestation(django_apps)

        attestation = write_cutover_attestation(
            eligible_room_count=0,
            active_room_count=0,
        )
        attestation.expires_at = timezone.now() - timedelta(seconds=1)
        attestation.save(update_fields=["expires_at"])
        with self.assertRaisesRegex(RuntimeError, "expired readiness attestation"):
            self.module.verify_cutover_attestation(django_apps)

        write_cutover_attestation(eligible_room_count=0, active_room_count=0)
        ChatRoom.objects.create()
        with self.assertRaisesRegex(RuntimeError, "relationship state changed"):
            self.module.verify_cutover_attestation(django_apps)

    def test_migration_independently_rejects_forged_mapping_counts(self):
        attestation = write_cutover_attestation(
            eligible_room_count=0,
            active_room_count=0,
        )
        attestation.eligible_room_count = 1
        attestation.active_room_count = 1
        attestation.save(update_fields=["eligible_room_count", "active_room_count"])
        with self.assertRaisesRegex(RuntimeError, "incomplete Matrix mappings"):
            self.module.verify_cutover_attestation(django_apps)

    @override_settings(MATRIX_SERVER_NAME="changed.test")
    def test_migration_rejects_configuration_mismatch(self):
        # Write against the unmodified setting, then change it only for verify.
        with override_settings(MATRIX_SERVER_NAME="matrix.test"):
            write_cutover_attestation(eligible_room_count=0, active_room_count=0)
        with self.assertRaisesRegex(RuntimeError, "configuration changed"):
            self.module.verify_cutover_attestation(django_apps)
