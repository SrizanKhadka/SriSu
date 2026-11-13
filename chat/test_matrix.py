from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import call, patch
from importlib import import_module

import jwt
import requests
from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import DeviceSession, UserModel
from chat.models import (
    MatrixCutoverAttestation,
    MatrixRoomMapping,
    MatrixUserMapping,
)
from chat.matrix_readiness import write_cutover_attestation
from chat.services.matrix_provisioning import (
    DisabledMatrixService,
    MatrixLoginSession,
    MatrixProvisionedRoom,
    MatrixServiceError,
    MatrixUnavailable,
    SynapseMatrixService,
    ensure_matrix_mapping_records,
    mark_matrix_mapping_revoke_pending,
    matrix_room_alias_localpart,
    provision_matrix_room,
    revoke_matrix_room,
    verify_active_matrix_room,
)
from social.models import CoupleConnectionModel, SingleConnectionModel
from social.services.relationship_service import accept_connection, end_connection
from tools.check_core_contracts import validate_contract


def make_user(index):
    return UserModel.objects.create_user(
        phone_number=f"+15005558{index:03}",
        full_name=f"Synthetic Matrix {index}",
        is_phone_verified=True,
        is_profile_complete=True,
    )


class FakeMatrixService:
    def __init__(self):
        self.calls = []
        self.verify_errors = []
        self.provision_errors = []
        self.revoke_errors = []
        self.before_provision = None
        self.before_verify = None
        self.before_revoke = None
        self.before_session = None
        self.missing_alias_result = "!resolved-synthetic-room:matrix.test"
        self.application_atomic_depth = len(connection.atomic_blocks)

    def _outside_transaction(self):
        # Django TestCase itself owns wrapper transactions. The service call
        # must not add an application-level atomic block on top of that depth.
        assert len(connection.atomic_blocks) == self.application_atomic_depth

    def provision_user(self, matrix_user_id):
        self._outside_transaction()
        self.calls.append(("user", matrix_user_id))
        return matrix_user_id

    def verify_policy(self):
        self._outside_transaction()
        self.calls.append(("policy",))

    def provision_room(self, *, room_alias, initiator_user_id, partner_user_id):
        self._outside_transaction()
        if self.before_provision is not None:
            self.before_provision()
        if self.provision_errors:
            raise self.provision_errors.pop(0)
        self.calls.append(("room", room_alias, initiator_user_id, partner_user_id))
        return MatrixProvisionedRoom(
            room_id=(
                "!synthetic-replacement:matrix.test"
                if "_e1:" not in room_alias
                else "!synthetic-room:matrix.test"
            ),
            room_alias=room_alias,
        )

    def verify_room(self, *, room_id, room_alias, member_user_ids):
        self._outside_transaction()
        if self.before_verify is not None:
            callback = self.before_verify
            self.before_verify = None
            callback()
        self.calls.append(
            ("verify", room_id, room_alias, tuple(sorted(member_user_ids)))
        )
        if self.verify_errors:
            raise self.verify_errors.pop(0)

    def issue_login_token(self, *, matrix_user_id, django_session_id, ttl_seconds):
        self._outside_transaction()
        if self.before_session is not None:
            callback = self.before_session
            self.before_session = None
            callback()
        self.calls.append(("session", matrix_user_id, django_session_id, ttl_seconds))
        return MatrixLoginSession(
            login_type="org.matrix.login.jwt",
            login_token="synthetic-login-token",
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
        )

    def revoke_room(self, *, room_id, room_alias, member_user_ids):
        self._outside_transaction()
        if self.before_revoke is not None:
            self.before_revoke()
        if self.revoke_errors:
            raise self.revoke_errors.pop(0)
        resolved = room_id if room_id is not None else self.missing_alias_result
        self.calls.append(
            ("revoke", resolved, room_alias, tuple(sorted(member_user_ids)))
        )
        return resolved


@override_settings(
    MATRIX_ENABLED=True,
    MATRIX_HOMESERVER_URL="https://matrix.test",
    MATRIX_SERVER_NAME="matrix.test",
    MATRIX_PROTOCOL_ID="matrix-e2ee-v1",
    MATRIX_SESSION_TTL_SECONDS=120,
    MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS=300,
)
class MatrixProvisioningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.first, self.second, self.outsider = [make_user(index) for index in range(3)]
        connection_row = CoupleConnectionModel.objects.create(
            sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number,
            connection_status="PENDING",
        )
        self.relationship = accept_connection(
            connection_id=connection_row.pk,
            actor=self.second,
        )
        self.room = self.relationship.chat_room
        self.session = DeviceSession.objects.create(
            user=self.first,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client = APIClient()
        self.client.force_authenticate(
            self.first,
            token={"sid": str(self.session.pk)},
        )
        self.url = f"/api/chat/v2/rooms/{self.room.pk}/matrix/session/"

    def test_acceptance_records_pending_mappings_and_disabled_callback_never_rolls_back(self):
        third = make_user(20)
        fourth = make_user(21)
        pending = CoupleConnectionModel.objects.create(
            sender_number=third.phone_number,
            receiver_number=fourth.phone_number,
            connection_status="PENDING",
        )
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=DisabledMatrixService(),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                accepted = accept_connection(connection_id=pending.pk, actor=fourth)

        accepted.connection.refresh_from_db()
        self.assertEqual(accepted.connection.connection_status, "ACCEPTED")
        mapping = MatrixRoomMapping.objects.get(chat_room=accepted.chat_room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.FAILED)
        self.assertEqual(mapping.last_error_code, "matrix_not_configured")
        self.assertEqual(
            set(MatrixUserMapping.objects.filter(
                user__in=[third, fourth]
            ).values_list("matrix_localpart", flat=True)),
            {f"srisu_user_{third.pk}", f"srisu_user_{fourth.pk}"},
        )

    def test_authorized_session_provisions_outside_transaction_and_returns_no_django_pii(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        data = response.data["data"]
        validate_contract("matrixSession", response.data)
        self.assertEqual(data["homeserver_url"], "https://matrix.test")
        self.assertEqual(data["room_id"], "!synthetic-room:matrix.test")
        self.assertEqual(data["membership_epoch"], 1)
        self.assertEqual(data["relationship_id"], self.relationship.connection.pk)
        self.assertEqual(data["self_user_id"], self.first.pk)
        self.assertEqual(data["partner_user_id"], self.second.pk)
        self.assertEqual(data["initiator_user_id"], min(self.first.pk, self.second.pk))
        self.assertEqual(data["login_token"], "synthetic-login-token")
        self.assertEqual(data["login_type"], "org.matrix.login.jwt")
        self.assertFalse(data["login_token_one_time"])
        self.assertFalse(data["refresh_token_enabled"])
        self.assertEqual(data["access_token_expires_in_seconds"], 300)
        self.assertNotIn(self.first.phone_number, str(data))
        self.assertNotIn(self.second.phone_number, str(data))
        self.assertEqual(MatrixRoomMapping.objects.get(chat_room=self.room).state, "active")
        self.assertTrue(any(call[0] == "session" for call in matrix.calls))

    def test_session_requires_current_django_device_session_and_current_membership(self):
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 401)

        outsider_session = DeviceSession.objects.create(
            user=self.outsider,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_authenticate(
            self.outsider,
            token={"sid": str(outsider_session.pk)},
        )
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 404)

    def test_session_rechecks_device_and_block_policy_after_remote_token_issuance(self):
        with patch("chat.api.matrix.matrix_bootstrap") as bootstrap:
            def revoke_session(*args, **kwargs):
                self.session.revoked_at = timezone.now()
                self.session.save(update_fields=["revoked_at"])
                return {"login_token": "must-not-be-returned"}

            bootstrap.side_effect = revoke_session
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("must-not-be-returned", str(response.data))

        self.session.revoked_at = None
        self.session.save(update_fields=["revoked_at"])
        with patch("chat.api.matrix.matrix_bootstrap") as bootstrap:
            def block_relationship(*args, **kwargs):
                SingleConnectionModel.objects.create(
                    sender_number=self.second.phone_number,
                    receiver_number=self.first.phone_number,
                    connection_status="BLOCKED",
                )
                return {"login_token": "must-not-be-returned"}

            bootstrap.side_effect = block_relationship
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("must-not-be-returned", str(response.data))

    def test_session_never_returns_bootstrap_for_rotated_mapping(self):
        matrix = FakeMatrixService()

        def rotate_during_token_issue():
            mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
            alias_localpart = matrix_room_alias_localpart(self.room.pk, 2)
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                membership_epoch=2,
                room_alias_localpart=alias_localpart,
                matrix_room_alias=f"#{alias_localpart}:matrix.test",
                matrix_room_id="!new-generation:matrix.test",
            )

        matrix.before_session = rotate_during_token_issue
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic-login-token", str(response.data))

    def test_blocked_partner_is_denied_and_reconciler_durably_revokes_mapping(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        block = SingleConnectionModel.objects.create(
            sender_number=self.second.phone_number,
            receiver_number=self.first.phone_number,
            connection_status="BLOCKED",
        )

        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 404)
        output = StringIO()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            call_command("reconcile_matrix_rooms", limit=10, stdout=output)

        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        self.assertTrue(SingleConnectionModel.objects.filter(pk=block.pk).exists())
        self.assertIn("revoked=1", output.getvalue())
        self.assertTrue(any(item[0] == "revoke" for item in matrix.calls))

    def test_relationship_reconciler_does_not_backfill_blocked_pair(self):
        MatrixRoomMapping.objects.filter(chat_room=self.room).delete()
        MatrixUserMapping.objects.filter(user__in=[self.first, self.second]).delete()
        SingleConnectionModel.objects.create(
            sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number,
            connection_status="BLOCKED",
        )

        output = StringIO()
        call_command("reconcile_relationship_rooms", apply=True, stdout=output)

        self.assertIn("mode=applied", output.getvalue())
        self.assertFalse(MatrixRoomMapping.objects.filter(chat_room=self.room).exists())
        self.assertFalse(
            MatrixUserMapping.objects.filter(user__in=[self.first, self.second]).exists()
        )

    def test_revoke_pending_without_saved_room_id_resolves_alias_before_completion(self):
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        mapping.state = MatrixRoomMapping.State.REVOKE_PENDING
        mapping.matrix_room_id = None
        mapping.available_at = timezone.now()
        mapping.save(update_fields=[
            "state",
            "matrix_room_id",
            "available_at",
            "updated_at",
        ])
        matrix = FakeMatrixService()

        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            call_command("reconcile_matrix_rooms", limit=10)

        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        self.assertEqual(mapping.matrix_room_id, "!resolved-synthetic-room:matrix.test")
        revoke = next(item for item in matrix.calls if item[0] == "revoke")
        self.assertIn(str(self.room.pk).replace("-", ""), revoke[2])

    def test_mapping_snapshot_survives_room_and_identity_deletion_for_revocation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        mapping_id = mapping.pk
        expected_localparts = {
            f"srisu_user_{self.first.pk}",
            f"srisu_user_{self.second.pk}",
        }
        self.room.delete()
        MatrixUserMapping.objects.filter(user__in=[self.first, self.second]).delete()

        tombstone = MatrixRoomMapping.objects.get(pk=mapping_id)
        self.assertIsNone(tombstone.chat_room_id)
        self.assertEqual(set(tombstone.member_matrix_localparts), expected_localparts)
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            call_command("reconcile_matrix_rooms", limit=10)

        tombstone.refresh_from_db()
        self.assertEqual(tombstone.state, MatrixRoomMapping.State.REVOKED)
        revoke = next(item for item in matrix.calls if item[0] == "revoke")
        self.assertEqual(
            set(revoke[3]),
            {f"@{localpart}:matrix.test" for localpart in expected_localparts},
        )

    def test_participant_replacement_preserves_old_snapshot_and_fails_closed(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        old_snapshot = list(mapping.member_matrix_localparts)
        self.room.user_two = self.outsider
        self.room.save(update_fields=["user_two", "updated_at"])

        ensure_matrix_mapping_records(self.room)

        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKE_PENDING)
        self.assertEqual(mapping.member_matrix_localparts, old_snapshot)
        self.assertEqual(mapping.last_error_code, "matrix_membership_changed")

    def test_inflight_participant_change_never_invites_or_orphans_new_user(self):
        matrix = FakeMatrixService()
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        old_second_matrix_id = f"@srisu_user_{self.second.pk}:matrix.test"
        outsider_matrix_id = f"@srisu_user_{self.outsider.pk}:matrix.test"

        def replace_participant_during_create():
            matrix.before_provision = None
            self.room.user_two = self.outsider
            self.room.save(update_fields=["user_two", "updated_at"])
            ensure_matrix_mapping_records(self.room)

        matrix.before_provision = replace_participant_during_create
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 503)
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        room_call = next(call for call in matrix.calls if call[0] == "room")
        revoke_call = next(call for call in matrix.calls if call[0] == "revoke")
        self.assertIn(old_second_matrix_id, room_call)
        self.assertNotIn(outsider_matrix_id, room_call)
        self.assertIn(old_second_matrix_id, revoke_call[3])
        self.assertNotIn(outsider_matrix_id, revoke_call[3])

    def test_unlink_queues_and_completes_matrix_room_revocation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            with self.captureOnCommitCallbacks(execute=True):
                end_connection(
                    connection_id=self.relationship.connection.pk,
                    actor=self.first,
                )

        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        self.assertTrue(any(call[0] == "revoke" for call in matrix.calls))
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 404)

    def test_retry_command_reconciles_failed_room(self):
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        mapping.state = MatrixRoomMapping.State.FAILED
        mapping.available_at = timezone.now()
        mapping.save(update_fields=["state", "available_at", "updated_at"])
        matrix = FakeMatrixService()
        output = StringIO()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            call_command("reconcile_matrix_rooms", limit=10, stdout=output)
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertIn("provisioned=1", output.getvalue())

    def test_stale_provision_failure_cannot_clobber_new_claim(self):
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        first = FakeMatrixService()
        second = FakeMatrixService()

        def complete_new_claim():
            first.before_provision = None
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                locked_at=timezone.now()
                - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS + 1),
            )
            self.assertTrue(provision_matrix_room(mapping.pk, service=second))

        first.before_provision = complete_new_claim
        first.provision_errors.append(MatrixUnavailable("matrix_remote_unavailable"))
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=first,
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.attempts, 2)
        self.assertEqual(mapping.last_error_code, "")

    def test_stale_same_generation_success_never_revokes_new_claim_room(self):
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        first = FakeMatrixService()
        second = FakeMatrixService()

        def complete_new_claim():
            first.before_provision = None
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                locked_at=timezone.now()
                - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS + 1),
            )
            self.assertTrue(provision_matrix_room(mapping.pk, service=second))

        first.before_provision = complete_new_claim
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=first,
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 1)
        self.assertEqual(mapping.pending_remote_revocations, [])
        self.assertFalse(any(call[0] == "revoke" for call in first.calls))

    def test_bootstrap_replaces_drifted_room_with_new_epoch_alias(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            matrix.verify_errors.append(
                MatrixServiceError("matrix_room_policy_mismatch")
            )
            response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertIn("_e2", mapping.room_alias_localpart)
        self.assertEqual(
            mapping.matrix_room_id,
            "!synthetic-replacement:matrix.test",
        )
        self.assertEqual(response.data["data"]["membership_epoch"], 2)
        self.assertTrue(any(item[0] == "revoke" for item in matrix.calls))

    def test_stale_successful_verification_cannot_attest_new_generation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        MatrixRoomMapping.objects.filter(pk=mapping.pk).update(remote_verified_at=None)

        def rotate_during_verify():
            alias_localpart = matrix_room_alias_localpart(self.room.pk, 2)
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                membership_epoch=2,
                room_alias_localpart=alias_localpart,
                matrix_room_alias=f"#{alias_localpart}:matrix.test",
                matrix_room_id="!new-generation:matrix.test",
                remote_verified_at=None,
                last_error_code="",
            )

        matrix.before_verify = rotate_during_verify
        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=matrix,
        ):
            with self.assertRaises(CommandError):
                call_command("matrix_status", require_ready=True, stdout=StringIO())

        mapping.refresh_from_db()
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertEqual(mapping.matrix_room_id, "!new-generation:matrix.test")
        self.assertIsNone(mapping.remote_verified_at)
        self.assertEqual(mapping.last_error_code, "")
        self.assertFalse(MatrixCutoverAttestation.objects.exists())

    def test_stale_drift_result_cannot_revoke_new_generation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)

        def rotate_during_verify():
            alias_localpart = matrix_room_alias_localpart(self.room.pk, 2)
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                membership_epoch=2,
                room_alias_localpart=alias_localpart,
                matrix_room_alias=f"#{alias_localpart}:matrix.test",
                matrix_room_id="!new-generation:matrix.test",
                replacement_pending=False,
            )

        matrix.before_verify = rotate_during_verify
        matrix.verify_errors.append(MatrixServiceError("matrix_room_policy_mismatch"))
        self.assertFalse(verify_active_matrix_room(mapping.pk, service=matrix))
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertFalse(mapping.replacement_pending)
        self.assertFalse(any(call[0] == "revoke" for call in matrix.calls))

    def test_stale_transient_verification_cannot_poison_new_generation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)

        def rotate_during_verify():
            alias_localpart = matrix_room_alias_localpart(self.room.pk, 2)
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                membership_epoch=2,
                room_alias_localpart=alias_localpart,
                matrix_room_alias=f"#{alias_localpart}:matrix.test",
                matrix_room_id="!new-generation:matrix.test",
                last_error_code="",
            )

        matrix.before_verify = rotate_during_verify
        matrix.verify_errors.append(MatrixUnavailable("matrix_remote_auth_failed"))
        self.assertFalse(verify_active_matrix_room(mapping.pk, service=matrix))
        mapping.refresh_from_db()
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertEqual(mapping.last_error_code, "")

    def test_transient_live_verification_failure_never_rotates_room(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            matrix.verify_errors.append(MatrixUnavailable("matrix_remote_unavailable"))
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 503)

        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 1)
        self.assertFalse(mapping.replacement_pending)
        self.assertFalse(any(item[0] == "revoke" for item in matrix.calls))

    def test_drift_replacement_retries_after_remote_revoke_failure(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            matrix.verify_errors.append(
                MatrixServiceError("matrix_room_policy_mismatch")
            )
            matrix.revoke_errors.append(MatrixUnavailable("matrix_remote_unavailable"))
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 503)

            mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
            self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKE_PENDING)
            self.assertTrue(mapping.replacement_pending)
            mapping.available_at = timezone.now()
            mapping.save(update_fields=["available_at", "updated_at"])
            call_command("reconcile_matrix_rooms", limit=10)
            mapping.refresh_from_db()
            self.assertEqual(mapping.state, MatrixRoomMapping.State.PENDING)
            self.assertEqual(mapping.membership_epoch, 2)
            call_command("reconcile_matrix_rooms", limit=10)

        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)

    def test_drift_replacement_stops_when_relationship_becomes_blocked(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            matrix.verify_errors.append(
                MatrixServiceError("matrix_room_policy_mismatch")
            )

            def block_during_revoke():
                matrix.before_revoke = None
                SingleConnectionModel.objects.create(
                    sender_number=self.first.phone_number,
                    receiver_number=self.second.phone_number,
                    connection_status="BLOCKED",
                )

            matrix.before_revoke = block_during_revoke
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 503)

        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        self.assertFalse(mapping.replacement_pending)
        self.assertEqual(mapping.membership_epoch, 1)

    def test_eligible_revoked_mapping_recovers_once_on_fresh_epoch(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            block = SingleConnectionModel.objects.create(
                sender_number=self.first.phone_number,
                receiver_number=self.second.phone_number,
                connection_status="BLOCKED",
            )
            call_command("reconcile_matrix_rooms", limit=10)
            mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
            self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)

            block.delete()
            matrix.provision_errors.append(MatrixUnavailable("matrix_remote_unavailable"))
            call_command("reconcile_matrix_rooms", limit=10)
            mapping.refresh_from_db()
            self.assertEqual(mapping.state, MatrixRoomMapping.State.FAILED)
            self.assertEqual(mapping.membership_epoch, 2)
            mapping.available_at = timezone.now()
            mapping.save(update_fields=["available_at", "updated_at"])
            call_command("reconcile_matrix_rooms", limit=10)

        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertIn("_e2", mapping.matrix_room_alias)

    def test_stale_revoke_completion_cannot_overwrite_new_generation(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        mark_matrix_mapping_revoke_pending(mapping)

        def activate_new_generation():
            alias_localpart = matrix_room_alias_localpart(self.room.pk, 2)
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                state=MatrixRoomMapping.State.ACTIVE,
                membership_epoch=2,
                room_alias_localpart=alias_localpart,
                matrix_room_alias=f"#{alias_localpart}:matrix.test",
                matrix_room_id="!new-generation:matrix.test",
                locked_at=None,
            )

        matrix.before_revoke = activate_new_generation
        self.assertFalse(revoke_matrix_room(mapping.pk, service=matrix))
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertEqual(mapping.matrix_room_id, "!new-generation:matrix.test")

    def test_create_revoke_barrier_durably_cleans_stale_created_room(self):
        matrix = FakeMatrixService()
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)

        def revoke_alias_before_create_returns():
            matrix.before_provision = None
            MatrixRoomMapping.objects.filter(pk=mapping.pk).update(
                state=MatrixRoomMapping.State.REVOKE_PENDING,
                locked_at=timezone.now()
                - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS + 1),
                available_at=timezone.now(),
            )
            matrix.missing_alias_result = None
            self.assertTrue(revoke_matrix_room(mapping.pk, service=matrix))
            matrix.revoke_errors.append(MatrixUnavailable("matrix_remote_unavailable"))

        matrix.before_provision = revoke_alias_before_create_returns
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 503)
        mapping.refresh_from_db()
        self.assertEqual(mapping.state, MatrixRoomMapping.State.REVOKED)
        self.assertEqual(len(mapping.pending_remote_revocations), 1)
        queued = list(mapping.pending_remote_revocations)
        queued[0]["available_at"] = timezone.now().isoformat()
        mapping.pending_remote_revocations = queued
        mapping.save(update_fields=["pending_remote_revocations", "updated_at"])

        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            call_command("reconcile_matrix_rooms", limit=10)
        mapping.refresh_from_db()
        self.assertEqual(mapping.pending_remote_revocations, [])
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertTrue(
            any(
                call[0] == "revoke" and call[1] == "!synthetic-room:matrix.test"
                for call in matrix.calls
            )
        )

    def test_worker_verifies_stale_active_room_and_recovers_drift(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
            MatrixRoomMapping.objects.filter(chat_room=self.room).update(
                remote_verified_at=None
            )
            matrix.verify_errors.append(
                MatrixServiceError("matrix_room_policy_mismatch")
            )
            output = StringIO()
            call_command("reconcile_matrix_rooms", limit=10, stdout=output)

        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.ACTIVE)
        self.assertEqual(mapping.membership_epoch, 2)
        self.assertIn("verified=0", output.getvalue())
        self.assertIn("provisioned=1", output.getvalue())

    def test_relationship_reconcile_apply_backfills_existing_matrix_mappings_without_network(self):
        MatrixRoomMapping.objects.filter(chat_room=self.room).delete()
        MatrixUserMapping.objects.filter(user__in=[self.first, self.second]).delete()

        dry_run = StringIO()
        call_command("reconcile_relationship_rooms", stdout=dry_run)
        self.assertIn("mode=dry-run", dry_run.getvalue())
        self.assertIn("needs_repair=1", dry_run.getvalue())
        self.assertFalse(MatrixRoomMapping.objects.filter(chat_room=self.room).exists())

        applied = StringIO()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            side_effect=AssertionError("reconciliation must not call Synapse"),
        ):
            call_command("reconcile_relationship_rooms", apply=True, stdout=applied)
        self.assertIn("mode=applied", applied.getvalue())
        self.assertIn("needs_repair=1", applied.getvalue())
        mapping = MatrixRoomMapping.objects.get(chat_room=self.room)
        self.assertEqual(mapping.state, MatrixRoomMapping.State.PENDING)
        self.assertEqual(
            MatrixUserMapping.objects.filter(user__in=[self.first, self.second]).count(),
            2,
        )

    def test_matrix_status_reports_readiness_without_secrets(self):
        output = StringIO()
        call_command("matrix_status", stdout=output)
        rendered = output.getvalue()
        self.assertIn("matrix_schema_ready=true", rendered)
        self.assertIn("matrix_required_migration_applied=true", rendered)
        self.assertIn("matrix_rooms_pending=1", rendered)
        self.assertIn("matrix_cutover_ready=false", rendered)
        self.assertNotIn("synthetic-login-token", rendered)
        self.assertNotIn("MATRIX_JWT_SECRET", rendered)

    def test_matrix_status_require_ready_enforces_exact_active_eligible_set(self):
        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=FakeMatrixService(),
        ):
            with self.assertRaises(CommandError):
                call_command("matrix_status", require_ready=True, stdout=StringIO())

        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)

        ready_output = StringIO()
        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=matrix,
        ):
            call_command("matrix_status", require_ready=True, stdout=ready_output)
        self.assertIn("matrix_cutover_ready=true", ready_output.getvalue())
        self.assertIn("matrix_eligible_missing_active=0", ready_output.getvalue())
        self.assertIn("matrix_live_verified=1", ready_output.getvalue())
        attestation = MatrixCutoverAttestation.objects.get(scope="matrix-cutover-v1")
        self.assertGreater(attestation.expires_at, attestation.verified_at)
        import_module(
            "chat.migrations.0006_destroy_legacy_chat_transport"
        ).verify_cutover_attestation(django_apps)

        SingleConnectionModel.objects.create(
            sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number,
            connection_status="BLOCKED",
        )
        blocked_output = StringIO()
        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=matrix,
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "matrix_status",
                    require_ready=True,
                    stdout=blocked_output,
                )
        self.assertIn("matrix_ineligible_active=1", blocked_output.getvalue())
        self.assertIn("matrix_cutover_ready=false", blocked_output.getvalue())
        self.assertFalse(MatrixCutoverAttestation.objects.exists())

    def test_matrix_status_rejects_eligible_relationship_missing_room(self):
        MatrixRoomMapping.objects.filter(chat_room=self.room).delete()
        self.room.delete()
        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=FakeMatrixService(),
        ):
            output = StringIO()
            with self.assertRaises(CommandError):
                call_command("matrix_status", require_ready=True, stdout=output)
        self.assertIn("matrix_eligible_relationship_rooms=1", output.getvalue())
        self.assertIn("matrix_eligible_missing_active=1", output.getvalue())
        self.assertFalse(MatrixCutoverAttestation.objects.exists())

    def test_matrix_status_and_migration_reject_missing_active_identity(self):
        matrix = FakeMatrixService()
        with patch(
            "chat.services.matrix_provisioning.get_matrix_service",
            return_value=matrix,
        ):
            self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 200)
        MatrixUserMapping.objects.filter(user=self.second).update(
            state=MatrixUserMapping.State.FAILED,
            matrix_user_id=None,
        )

        with patch(
            "chat.management.commands.matrix_status.get_matrix_service",
            return_value=matrix,
        ):
            output = StringIO()
            with self.assertRaises(CommandError):
                call_command("matrix_status", require_ready=True, stdout=output)
        self.assertIn("matrix_eligible_identity_missing=1", output.getvalue())
        self.assertFalse(MatrixCutoverAttestation.objects.exists())

        write_cutover_attestation(eligible_room_count=1, active_room_count=1)
        with self.assertRaisesRegex(RuntimeError, "incomplete Matrix mappings"):
            import_module(
                "chat.migrations.0006_destroy_legacy_chat_transport"
            ).verify_cutover_attestation(django_apps)


@override_settings(
    DEBUG=True,
    MATRIX_ENABLED=True,
    MATRIX_HOMESERVER_URL="http://matrix.test:8008",
    MATRIX_INTERNAL_HOMESERVER_URL="http://synapse:8008",
    MATRIX_SERVER_NAME="matrix.test",
    MATRIX_JWT_SECRET="synthetic-test-matrix-secret-with-sufficient-length",
    MATRIX_JWT_ALGORITHM="HS256",
    MATRIX_JWT_ISSUER="srisu-test",
    MATRIX_JWT_AUDIENCE="srisu-matrix-test",
    MATRIX_HTTP_TIMEOUT_SECONDS=3,
    MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS=300,
    MATRIX_PROVISIONING_LOCALPART="srisu_provisioner",
    MATRIX_ROOM_VERSION="11",
)
class SynapseMatrixServiceTests(TestCase):
    initiator = "@srisu_user_1:matrix.test"
    partner = "@srisu_user_2:matrix.test"
    alias = "#srisu_chat_0123456789abcdef0123456789abcdef_e1:matrix.test"
    room_id = "!private-room:matrix.test"

    def setUp(self):
        self.service = SynapseMatrixService()

    def test_login_jwt_has_bounded_expected_claims_and_no_raw_session(self):
        session = self.service.issue_login_token(
            matrix_user_id=self.initiator,
            django_session_id="django-session-private-value",
            ttl_seconds=120,
        )
        claims = jwt.decode(
            session.login_token,
            "synthetic-test-matrix-secret-with-sufficient-length",
            algorithms=["HS256"],
            issuer="srisu-test",
            audience="srisu-matrix-test",
        )
        self.assertEqual(session.login_type, "org.matrix.login.jwt")
        self.assertEqual(claims["sub"], "srisu_user_1")
        self.assertEqual(claims["iss"], "srisu-test")
        self.assertEqual(claims["aud"], "srisu-matrix-test")
        self.assertEqual(claims["exp"] - claims["iat"], 120)
        self.assertNotIn("django-session-private-value", session.login_token)
        self.assertNotEqual(claims["srisu_sid_hash"], "django-session-private-value")

    def _state_events(
        self,
        *,
        creator=None,
        encryption_algorithm="m.megolm.v1.aes-sha2",
        extra_power_user=None,
        extra_event_level=None,
    ):
        power = self.service._power_levels([self.initiator, self.partner])
        if extra_power_user:
            power["users"][extra_power_user] = 100
        if extra_event_level is not None:
            power["events"]["com.example.unreviewed_state"] = extra_event_level
        return [
            {
                "type": "m.room.create",
                "state_key": "",
                "sender": creator or self.service.provisioning_user_id,
                "content": {"m.federate": False},
            },
            {
                "type": "m.room.encryption",
                "state_key": "",
                "sender": self.service.provisioning_user_id,
                "content": {"algorithm": encryption_algorithm},
            },
            {
                "type": "m.room.join_rules",
                "state_key": "",
                "sender": self.service.provisioning_user_id,
                "content": {"join_rule": "invite"},
            },
            {
                "type": "m.room.guest_access",
                "state_key": "",
                "sender": self.service.provisioning_user_id,
                "content": {"guest_access": "forbidden"},
            },
            {
                "type": "m.room.history_visibility",
                "state_key": "",
                "sender": self.service.provisioning_user_id,
                "content": {"history_visibility": "invited"},
            },
            {
                "type": "m.room.power_levels",
                "state_key": "",
                "sender": self.service.provisioning_user_id,
                "content": power,
            },
        ]

    def _room_request(
        self,
        *,
        alias_state,
        seen,
        creator=None,
        encryption_algorithm="m.megolm.v1.aes-sha2",
        rogue_member=None,
        extra_power_user=None,
        extra_event_level=None,
    ):
        def request(method, path, **kwargs):
            seen.append((method, path, kwargs))
            if "/directory/room/" in path:
                if alias_state["exists"]:
                    return 200, {"room_id": self.room_id}
                return 404, {}
            if path.endswith("/createRoom"):
                body = kwargs["body"]
                self.assertEqual(kwargs["access_token"], "provisioner-token")
                self.assertEqual(body["visibility"], "private")
                self.assertEqual(body["preset"], "private_chat")
                self.assertEqual(body["room_version"], "11")
                self.assertEqual(body["creation_content"], {"m.federate": False})
                self.assertEqual(body["invite"], [self.initiator, self.partner])
                self.assertEqual(
                    body["power_level_content_override"],
                    self.service._power_levels([self.initiator, self.partner]),
                )
                self.assertEqual(
                    body["power_level_content_override"]["users"][
                        self.service.provisioning_user_id
                    ],
                    100,
                )
                alias_state["exists"] = True
                return 200, {"room_id": self.room_id}
            if "/directory/list/room/" in path:
                if method == "PUT":
                    self.assertEqual(kwargs["access_token"], "provisioner-token")
                    self.assertEqual(kwargs["body"], {"visibility": "private"})
                    return 200, {}
                return 200, {"visibility": "private"}
            if path.endswith("/state"):
                return 200, self._state_events(
                    creator=creator,
                    encryption_algorithm=encryption_algorithm,
                    extra_power_user=extra_power_user,
                    extra_event_level=extra_event_level,
                )
            if path.endswith("/members"):
                chunk = [
                    {"state_key": self.initiator, "content": {"membership": "join"}},
                    {"state_key": self.partner, "content": {"membership": "join"}},
                    {
                        "state_key": self.service.provisioning_user_id,
                        "content": {"membership": "leave"},
                    },
                ]
                if rogue_member:
                    chunk.append(
                        {"state_key": rogue_member, "content": {"membership": "join"}}
                    )
                return 200, {"chunk": chunk}
            if "/join/" in path:
                self.assertIn(kwargs["access_token"], {"initiator-token", "partner-token"})
                return 200, {"room_id": self.room_id}
            if path.endswith("/leave"):
                self.assertEqual(kwargs["access_token"], "provisioner-token")
                return 200, {}
            raise AssertionError(f"Unexpected request: {method} {path}")

        return request

    def test_create_room_is_private_non_federated_encrypted_and_partner_joins(self):
        seen = []
        alias_state = {"exists": False}

        with (
            patch.object(
                self.service,
                "_login",
                side_effect=["provisioner-token", "initiator-token", "partner-token"],
            ),
            patch.object(self.service, "_logout") as logout,
            patch.object(
                self.service,
                "_request",
                side_effect=self._room_request(alias_state=alias_state, seen=seen),
            ),
        ):
            room = self.service.provision_room(
                room_alias=self.alias,
                initiator_user_id=self.initiator,
                partner_user_id=self.partner,
            )

        self.assertEqual(room, MatrixProvisionedRoom(self.room_id, self.alias))
        self.assertTrue(any(path.endswith("/createRoom") for _, path, _ in seen))
        logout.assert_has_calls([
            call("partner-token"),
            call("initiator-token"),
            call("provisioner-token"),
        ])

    def test_existing_alias_is_verified_before_join_and_not_recreated(self):
        seen = []

        with (
            patch.object(
                self.service,
                "_login",
                side_effect=["provisioner-token", "initiator-token", "partner-token"],
            ),
            patch.object(self.service, "_logout"),
            patch.object(
                self.service,
                "_request",
                side_effect=self._room_request(alias_state={"exists": True}, seen=seen),
            ),
        ):
            self.service.provision_room(
                room_alias=self.alias,
                initiator_user_id=self.initiator,
                partner_user_id=self.partner,
            )
        paths = [path for _, path, _ in seen]
        self.assertFalse(any(path.endswith("/createRoom") for path in paths))
        self.assertTrue(any(path.endswith("/state") for path in paths))
        self.assertTrue(any(path.endswith("/members") for path in paths))

    def test_live_verification_authenticates_and_binds_alias_to_room_id(self):
        seen = []
        with (
            patch.object(
                self.service,
                "_login",
                side_effect=["provisioner-token", "initiator-token"],
            ),
            patch.object(self.service, "_logout") as logout,
            patch.object(
                self.service,
                "_request",
                side_effect=self._room_request(
                    alias_state={"exists": True},
                    seen=seen,
                ),
            ),
        ):
            self.service.verify_room(
                room_id=self.room_id,
                room_alias=self.alias,
                member_user_ids=[self.initiator, self.partner],
            )

        paths = [path for _, path, _ in seen]
        self.assertTrue(any("/directory/room/" in path for path in paths))
        self.assertTrue(any(path.endswith("/state") for path in paths))
        self.assertTrue(any(path.endswith("/members") for path in paths))
        logout.assert_has_calls([call("initiator-token"), call("provisioner-token")])

    def test_policy_probe_requires_exact_loaded_module_identity(self):
        expected = {
            "policy_version": "srisu-room-policy-v1",
            "server_name": "matrix.test",
            "provisioning_user_id": "@srisu_provisioner:matrix.test",
        }
        with patch.object(self.service, "_request", return_value=(200, expected)):
            self.service.verify_policy()
        with patch.object(
            self.service,
            "_request",
            return_value=(200, {**expected, "policy_version": "old-policy"}),
        ):
            with self.assertRaisesRegex(
                MatrixUnavailable,
                "matrix_policy_probe_mismatch",
            ):
                self.service.verify_policy()

    def test_policy_mismatch_fails_closed_and_logs_out_temporary_devices(self):
        with (
            patch.object(
                self.service,
                "_login",
                side_effect=["provisioner-token", "initiator-token", "partner-token"],
            ),
            patch.object(self.service, "_logout") as logout,
            patch.object(
                self.service,
                "_request",
                side_effect=self._room_request(
                    alias_state={"exists": True},
                    seen=[],
                    encryption_algorithm="not-approved",
                ),
            ),
        ):
            with self.assertRaisesRegex(MatrixServiceError, "matrix_room_policy_mismatch"):
                self.service.provision_room(
                    room_alias=self.alias,
                    initiator_user_id=self.initiator,
                    partner_user_id=self.partner,
                )
        logout.assert_has_calls([
            call("partner-token"),
            call("initiator-token"),
            call("provisioner-token"),
        ])

    def test_wrong_creator_or_extra_non_leave_member_fails_closed(self):
        cases = [
            {"creator": "@not-the-provisioner:matrix.test"},
            {"rogue_member": "@rogue:matrix.test"},
            {"extra_power_user": "@dormant-admin:matrix.test"},
            {"extra_event_level": 0},
        ]
        for case in cases:
            with self.subTest(case=case):
                with (
                    patch.object(
                        self.service,
                        "_login",
                        side_effect=[
                            "provisioner-token",
                            "initiator-token",
                            "partner-token",
                        ],
                    ),
                    patch.object(self.service, "_logout"),
                    patch.object(
                        self.service,
                        "_request",
                        side_effect=self._room_request(
                            alias_state={"exists": True},
                            seen=[],
                            **case,
                        ),
                    ),
                ):
                    with self.assertRaisesRegex(
                        MatrixServiceError, "matrix_room_policy_mismatch"
                    ):
                        self.service.provision_room(
                            room_alias=self.alias,
                            initiator_user_id=self.initiator,
                            partner_user_id=self.partner,
                        )

    def test_revoke_makes_both_members_leave_and_cleans_up_sessions(self):
        requests_seen = []

        def request(method, path, **kwargs):
            requests_seen.append((method, path, kwargs))
            return 200, {}

        with (
            patch.object(self.service, "_login", side_effect=["initiator-token", "partner-token"]),
            patch.object(self.service, "_logout") as logout,
            patch.object(self.service, "_request", side_effect=request),
        ):
            self.service.revoke_room(
                room_id=self.room_id,
                room_alias=self.alias,
                member_user_ids=[self.initiator, self.partner],
            )
        leaves = [item for item in requests_seen if item[1].endswith("/leave")]
        self.assertEqual(len(leaves), 2)
        self.assertEqual(
            {item[2]["access_token"] for item in leaves},
            {"initiator-token", "partner-token"},
        )
        logout.assert_has_calls([call("partner-token"), call("initiator-token")])

    def test_revoke_resolves_deterministic_alias_when_room_id_was_not_persisted(self):
        requests_seen = []

        def request(method, path, **kwargs):
            requests_seen.append((method, path, kwargs))
            if "/directory/room/" in path:
                return 200, {"room_id": self.room_id}
            if path.endswith("/leave"):
                return 200, {}
            raise AssertionError(path)

        with (
            patch.object(
                self.service,
                "_login",
                side_effect=["provisioner-token", "initiator-token", "partner-token"],
            ),
            patch.object(self.service, "_logout") as logout,
            patch.object(self.service, "_request", side_effect=request),
        ):
            resolved = self.service.revoke_room(
                room_id=None,
                room_alias=self.alias,
                member_user_ids=[self.initiator, self.partner],
            )
        self.assertEqual(resolved, self.room_id)
        self.assertEqual(len([item for item in requests_seen if item[1].endswith("/leave")]), 2)
        logout.assert_has_calls([
            call("provisioner-token"),
            call("partner-token"),
            call("initiator-token"),
        ])

    def test_remote_create_then_logout_failure_reuses_alias_on_retry(self):
        seen = []
        alias_state = {"exists": False}
        request = self._room_request(alias_state=alias_state, seen=seen)
        logout_results = [
            MatrixServiceError("matrix_remote_unavailable"),
            None,
            None,
            None,
            None,
            None,
        ]
        with (
            patch.object(
                self.service,
                "_login",
                side_effect=[
                    "provisioner-token",
                    "initiator-token",
                    "partner-token",
                    "provisioner-token",
                    "initiator-token",
                    "partner-token",
                ],
            ),
            patch.object(self.service, "_logout", side_effect=logout_results),
            patch.object(self.service, "_request", side_effect=request),
        ):
            with self.assertRaisesRegex(
                MatrixServiceError, "matrix_provisioning_logout_failed"
            ):
                self.service.provision_room(
                    room_alias=self.alias,
                    initiator_user_id=self.initiator,
                    partner_user_id=self.partner,
                )
            room = self.service.provision_room(
                room_alias=self.alias,
                initiator_user_id=self.initiator,
                partner_user_id=self.partner,
            )

        self.assertEqual(room, MatrixProvisionedRoom(self.room_id, self.alias))
        self.assertEqual(
            len([path for _, path, _ in seen if path.endswith("/createRoom")]),
            1,
        )

    def test_http_timeout_and_rejection_are_sanitized(self):
        with patch.object(
            self.service.http,
            "request",
            side_effect=requests.Timeout("secret URL detail"),
        ):
            with self.assertRaisesRegex(MatrixUnavailable, "matrix_remote_unavailable"):
                self.service._request("GET", "/_matrix/client/versions")

        for status_code in (429, 500):
            with self.subTest(status_code=status_code):
                response = SimpleNamespace(
                    status_code=status_code,
                    content=b'{"error":"private upstream detail"}',
                    json=lambda: {"error": "private upstream detail"},
                )
                with patch.object(
                    self.service.http,
                    "request",
                    return_value=response,
                ) as request:
                    with self.assertRaisesRegex(
                        MatrixUnavailable,
                        "matrix_remote_unavailable",
                    ):
                        self.service._request("GET", "/_matrix/client/versions")
                self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertFalse(self.service.http.trust_env)

    def test_verify_login_403_is_unavailable_not_room_drift(self):
        response = SimpleNamespace(
            status_code=403,
            content=b'{"errcode":"M_FORBIDDEN"}',
            json=lambda: {"errcode": "M_FORBIDDEN"},
        )
        with patch.object(self.service.http, "request", return_value=response):
            with self.assertRaisesRegex(
                MatrixUnavailable,
                "matrix_remote_auth_failed",
            ):
                self.service.verify_room(
                    room_id=self.room_id,
                    room_alias=self.alias,
                    member_user_ids=[self.initiator, self.partner],
                )

    @override_settings(DEBUG=False, MATRIX_HOMESERVER_URL="http://matrix.test:8008")
    def test_plain_http_public_origin_is_rejected_outside_debug(self):
        with self.assertRaisesRegex(MatrixUnavailable, "matrix_public_url_invalid"):
            SynapseMatrixService()

    @override_settings(MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS=901)
    def test_access_token_lifetime_above_revocation_bound_is_rejected(self):
        with self.assertRaisesRegex(MatrixUnavailable, "matrix_access_ttl_invalid"):
            SynapseMatrixService()

    @override_settings(MATRIX_ROOM_VERSION="12")
    def test_unreviewed_room_version_is_rejected(self):
        with self.assertRaisesRegex(MatrixUnavailable, "matrix_room_version_unsupported"):
            SynapseMatrixService()

    def test_partial_temporary_login_is_logged_out_when_second_login_fails(self):
        with (
            patch.object(
                self.service,
                "_login",
                side_effect=[
                    "provisioner-token",
                    MatrixUnavailable("matrix_remote_unavailable"),
                ],
            ),
            patch.object(self.service, "_logout") as logout,
        ):
            with self.assertRaisesRegex(MatrixUnavailable, "matrix_remote_unavailable"):
                self.service.provision_room(
                    room_alias=self.alias,
                    initiator_user_id=self.initiator,
                    partner_user_id=self.partner,
                )
        logout.assert_called_once_with("provisioner-token")
