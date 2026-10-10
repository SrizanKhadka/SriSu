"""Opaque synthetic envelopes test transport; native suites test actual crypto."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

from django.core.cache import cache
from django.db import connections
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from authentication.models import DeviceSession
from . import protocol, serializers
from .models import BundleClaim, Change, Device, Message, MutationHead, Operation, PreKey
from .tests import Fixtures


def encoded(size, marker=1):
    return base64.b64encode(bytes([marker]) * size).decode("ascii")


def registration(count=4):
    # Size-correct, non-secret placeholders. Clients MUST verify library signatures.
    return {"device_id": str(uuid4()), "replace_device_id": None, "registration_id": 123,
        "identity_key": encoded(33),
        "signed_prekey": {"id": 1, "public_key": encoded(33, 2), "signature": encoded(64)},
        "ec_prekeys": [{"id": i, "public_key": encoded(33, i)} for i in range(1, count + 1)],
        "kyber_prekeys": [{"id": i, "public_key": encoded(1569, i), "signature": encoded(64)} for i in range(1, count + 1)]}


class ProtocolFixtures(Fixtures):
    def assert_contract(self,name,value):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator,FormatChecker
        schema=json.loads((Path(__file__).resolve().parents[1]/"contracts/couple-chat-1/schema.json").read_text())
        schema["$ref"]="#/$defs/"+name
        Draft202012Validator(schema,format_checker=FormatChecker()).validate(value)

    def setUp(self):
        super().setUp()
        cache.clear()
        self.room = self.accept()[2]
        self.first_client, self.second_client, self.third_client = [self.client_for(u) for u in (self.first, self.second, self.third)]
        self.first_sid = AccessToken(self.first_client._credentials["HTTP_AUTHORIZATION"][7:])["sid"]
        self.second_sid = AccessToken(self.second_client._credentials["HTTP_AUTHORIZATION"][7:])["sid"]
        self.first_keys, self.second_keys = registration(), registration()
        for client, keys in ((self.first_client, self.first_keys), (self.second_client, self.second_keys)):
            response = client.post("/api/couple-chat/v1/devices/", keys, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assert_contract("device",response.data["data"])
        self.path = f"/api/couple-chat/v1/rooms/{self.room.pk}/"

    def envelope(self, *, second=False, kind="message", target=None, version=None, **extra):
        operation_id = str(uuid4())
        sender, recipient = (self.second_keys, self.first_keys) if second else (self.first_keys, self.second_keys)
        return {"device_id": sender["device_id"], "recipient_device_id": recipient["device_id"],
                "operation_id": operation_id, "kind": kind, "target_id": target or operation_id,
                "reply_to": None, "version": version if version is not None else (0 if kind == "message" else 1),
                "signal_type": 3, "ciphertext": encoded(64), **extra}

    def send(self, body, client=None, status=200):
        response = (client or self.first_client).post(self.path + "operations/", body, format="json")
        self.assertEqual(response.status_code, status, response.data)
        if status==200:self.assert_contract("acceptance",response.data["data"])
        return response

    def sync(self, *, client=None, device=None, **query):
        response=(client or self.first_client).get(self.path + "changes/", {
            "device_id": device or self.first_keys["device_id"], **query})
        if response.status_code==200:self.assert_contract("sync",response.data["data"])
        return response

    def claim(self, *, operation=None):
        return {"device_id": self.first_keys["device_id"], "recipient_device_id": self.second_keys["device_id"],
                "operation_id": operation or str(uuid4())}


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class ProtocolTests(ProtocolFixtures, APITestCase):
    def test_reply_tombstones_and_mutation_routing_keep_original_authenticated_fields(self):
        original = self.envelope()
        self.send(original)
        self.send(self.envelope(kind="delete", target=original["target_id"]))
        reply = self.envelope(second=True, reply_to=original["target_id"])
        self.send(reply, self.second_client)
        reaction = self.envelope(kind="reaction", target=reply["target_id"])
        self.send(reaction)
        operations = {item["operation"]["operation_id"]: item["operation"] for item in self.sync().data["data"]["changes"] if item["operation"]}
        self.assertEqual(operations[reply["operation_id"]]["reply_to"], original["target_id"])
        self.assertIsNone(operations[reaction["operation_id"]]["reply_to"])

    def test_participant_key_lookup_is_room_scoped_and_keeps_retired_public_identity(self):
        path = self.path + f"devices/{self.second_keys['device_id']}/"
        response = self.first_client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["identity_key"], self.second_keys["identity_key"])
        self.assertNotIn("ec_prekeys", response.data["data"])
        self.assertEqual(self.third_client.get(path).status_code, 404)
        self.assertEqual(self.first_client.get(self.path + f"devices/{uuid4()}/").status_code, 404)
        replacement = registration()
        replacement["replace_device_id"] = self.second_keys["device_id"]
        self.assertEqual(self.second_client.post("/api/couple-chat/v1/devices/", replacement, format="json").status_code, 200)
        self.assertEqual(self.first_client.get(path).data, response.data)
        self.room.revoked_at = timezone.now()
        self.room.save(update_fields=["revoked_at"])
        self.assertEqual(self.first_client.get(path).status_code, 404)

    def test_both_directions_retry_is_original_result_and_conflict_is_rejected(self):
        for second, client in ((False, self.first_client), (True, self.second_client)):
            body = self.envelope(second=second)
            accepted = self.send(body, client).data
            self.assertEqual(self.send(body, client).data, accepted)
            conflict = self.send({**body, "ciphertext": encoded(64, 2)}, client, status=409)
            self.assertEqual(conflict.data["chat_code"], "operation_conflict")
        self.assertEqual(Operation.objects.count(), 2)
        self.assertEqual(Message.objects.count(), 2)

    def test_all_commands_deny_unrelated_user_and_device_spoofing(self):
        body = self.envelope()
        self.send(body, self.third_client, status=404)
        self.send({**body, "device_id": self.second_keys["device_id"]}, status=409)
        self.assertEqual(self.third_client.post(self.path + "bundles/claim/", self.claim(), format="json").status_code, 404)
        self.assertEqual(self.sync(client=self.third_client).status_code, 404)
        self.send({**body, "actor_id": self.second.pk}, status=400)

    def test_bundle_claim_is_idempotent_consumes_keys_once_and_is_bounded(self):
        body = self.claim()
        response = self.first_client.post(self.path + "bundles/claim/", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, self.first_client.post(self.path + "bundles/claim/", body, format="json").data)
        self.assertEqual(PreKey.objects.filter(device_id=self.second_keys["device_id"], consumed_at__isnull=False).count(), 2)
        for _ in range(3):
            self.assertEqual(self.first_client.post(self.path + "bundles/claim/", self.claim(), format="json").status_code, 200)
        exhausted = self.first_client.post(self.path + "bundles/claim/", self.claim(), format="json")
        self.assertEqual(exhausted.status_code, 409)
        self.assertEqual(BundleClaim.objects.count(), 4)

    def test_prekey_republication_does_not_resurrect_consumed_keys_or_rotate_backwards(self):
        self.first_client.post(self.path + "bundles/claim/", self.claim(), format="json")
        data = {key: self.second_keys[key] for key in ("device_id", "signed_prekey", "ec_prekeys", "kyber_prekeys")}
        data["operation_id"] = str(uuid4())
        before = PreKey.objects.filter(consumed_at__isnull=False).count()
        for _ in range(2):
            self.assertEqual(self.second_client.post("/api/couple-chat/v1/devices/keys/", data, format="json").status_code, 200)
        self.assertEqual(PreKey.objects.filter(consumed_at__isnull=False).count(), before)
        bad = {**data, "operation_id": str(uuid4()), "signed_prekey": {**data["signed_prekey"], "public_key": encoded(33, 3)}}
        self.assertEqual(self.second_client.post("/api/couple-chat/v1/devices/keys/", bad, format="json").status_code, 409)

    def test_registration_retry_and_explicit_replacement_do_not_redirect_old_ciphertext(self):
        original = self.envelope()
        self.send(original)
        self.assertEqual(self.first_client.post("/api/couple-chat/v1/devices/", self.first_keys, format="json").status_code, 200)
        replacement = registration()
        self.assertEqual(self.first_client.post("/api/couple-chat/v1/devices/", replacement, format="json").status_code, 409)
        replacement["replace_device_id"] = self.first_keys["device_id"]
        self.assertEqual(self.first_client.post("/api/couple-chat/v1/devices/", replacement, format="json").status_code, 200)
        self.send(original, status=409)
        history = self.sync(device=replacement["device_id"])
        self.assertEqual(history.status_code, 200, history.data)
        operation = next(row["operation"] for row in history.data["data"]["changes"] if row["kind"] == "operation")
        self.assertIsNone(operation["ciphertext"])
        self.assertTrue(operation["unavailable"])

    def test_another_login_cannot_adopt_an_existing_chat_device(self):
        other = self.client_for(self.first)
        self.send(self.envelope(), other, status=409)
        state = other.get("/api/couple-chat/v1/state/").data["data"]["devices"]
        self.assertFalse(state["session_owns_device"])
        self.assertEqual(other.post("/api/couple-chat/v1/devices/", self.first_keys, format="json").status_code, 409)

    def test_revocation_and_expiry_rechecked_for_own_and_partner_sessions(self):
        DeviceSession.objects.filter(pk=self.second_sid).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.send(self.envelope(), status=409)
        DeviceSession.objects.filter(pk=self.first_sid).update(revoked_at=timezone.now())
        self.send(self.envelope(), status=401)
        self.assertEqual(self.sync().status_code, 401)

    def test_retries_after_partner_replacement_return_original_acceptance_only(self):
        body = self.envelope()
        accepted = self.send(body).data
        new = {**registration(), "replace_device_id": self.second_keys["device_id"]}
        self.assertEqual(self.second_client.post("/api/couple-chat/v1/devices/", new, format="json").status_code, 200)
        self.assertEqual(self.send(body).data, accepted)
        self.send(self.envelope(), status=409)

    def test_paginated_ordered_log_has_no_gaps_and_rejects_invalid_cursors(self):
        for _ in range(3):
            self.send(self.envelope())
        cursor = 0
        sequences = []
        while True:
            page = self.sync(after=cursor, limit=2)
            self.assertEqual(page.status_code, 200, page.data)
            page = page.data["data"]
            sequences.extend(row["sequence"] for row in page["changes"])
            cursor = page["cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(sequences, list(range(1, cursor + 1)))
        self.assertEqual(self.sync(after=cursor + 1).status_code, 409)
        for query in ({"limit": 101}, {"after": -1}, {"unexpected": 1}):
            self.assertEqual(self.sync(**query).status_code, 400)

    def test_desired_state_versions_prevent_late_reaction_restoring_old_state(self):
        message = self.envelope()
        self.send(message)
        for version, applied in ((3, True), (1, False), (2, False), (4, True)):
            result = self.send(self.envelope(kind="reaction", target=message["target_id"], version=version))
            self.assertEqual(result.data["data"]["applied"], applied)
        self.assertEqual(MutationHead.objects.get().version, 4)
        # Stale envelopes still appear in order so the recipient advances its ratchet.
        self.assertEqual(Operation.objects.count(), 5)

    def test_receipts_are_recipient_only_and_independent_monotonic_kinds(self):
        message = self.envelope()
        self.send(message)
        self.send(self.envelope(kind="receipt.read", target=message["target_id"]), status=403)
        for kind in ("receipt.read", "receipt.delivered", "receipt.played"):
            self.send(self.envelope(second=True, kind=kind, target=message["target_id"]), self.second_client)
        self.assertEqual(MutationHead.objects.count(), 3)

    def test_delete_authorization_replies_and_tombstones_survive_catchup(self):
        message = self.envelope()
        self.send(message)
        self.send(self.envelope(second=True, reply_to=message["target_id"]), self.second_client)
        self.send(self.envelope(second=True, kind="delete", target=message["target_id"]), self.second_client, status=403)
        self.send(self.envelope(kind="delete", target=message["target_id"]))
        self.send(self.envelope(reply_to=message["target_id"]))
        self.send(self.envelope(reply_to=str(uuid4())), status=404)
        self.send(self.envelope(kind="reaction", target=message["target_id"]), status=409)
        self.send(message)  # uncertain original acknowledgement cannot resurrect it
        self.assertIsNotNone(Message.objects.get(pk=message["target_id"]).deleted_at)
        history = self.sync().data["data"]["changes"]
        creation = next(row for row in history if row.get("operation") and row["operation"]["operation_id"] == message["operation_id"])
        self.assertTrue(creation["operation"]["target_deleted"])

    def test_cross_room_targets_are_denied_and_sender_is_server_derived(self):
        body = self.envelope(kind="reaction", target=str(uuid4()))
        self.send(body, status=404)
        message = self.envelope()
        self.send(message)
        self.assertEqual(Operation.objects.get().actor, self.first)
        self.send({**self.envelope(), "target_id": message["target_id"]}, status=400)

    def test_malformed_and_oversized_requests_leave_no_accepted_operation(self):
        for edit in ({"signal_type": 7}, {"ciphertext": "plaintext"}, {"ciphertext": encoded(65537)},
                     {"ciphertext": encoded(31)}, {"version": 1}, {"kind": "unknown"}):
            self.send({**self.envelope(), **edit}, status=400)
        oversized = self.first_client.post(self.path + "operations/", " " * 131073, content_type="application/json")
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(Operation.objects.count(), 0)

    def test_failed_command_rolls_back_message_sequence_and_dispatch(self):
        from unittest.mock import patch
        before = Change.objects.count()
        with patch("couple_chat.protocol.Operation.objects.create", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.send(self.envelope())
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Change.objects.count(), before)
        self.room.refresh_from_db()
        self.assertEqual(self.room.sequence, before)

    def test_block_and_unlink_stop_commands_and_sync(self):
        from social.models import SingleConnectionModel
        SingleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number, connection_status="BLOCKED")
        self.send(self.envelope(), status=404)
        self.assertEqual(self.sync().status_code, 404)


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class ProtocolRaceTests(ProtocolFixtures, APITransactionTestCase):
    def concurrent(self, action):
        barrier = Barrier(2)
        def worker(index):
            try:
                barrier.wait(timeout=10)
                return action(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, [0, 1]))

    @skipUnlessDBFeature("has_select_for_update")
    def test_duplicate_send_race_commits_one_operation(self):
        body = serializers.Envelope(data=self.envelope())
        body.is_valid(raise_exception=True)
        values = self.concurrent(lambda _: protocol.accept_operation(self.first, self.first_sid, self.room.pk, body.validated_data))
        self.assertEqual(values[0], values[1])
        self.assertEqual(Operation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_claims_never_reuse_a_one_time_prekey(self):
        bodies = [serializers.Claim(data=self.claim()) for _ in range(2)]
        for body in bodies:
            body.is_valid(raise_exception=True)
        values = self.concurrent(lambda i: protocol.claim_bundle(self.first, self.first_sid, self.room.pk, bodies[i].validated_data))
        self.assertNotEqual(values[0]["ec_prekey"]["id"], values[1]["ec_prekey"]["id"])
        self.assertNotEqual(values[0]["kyber_prekey"]["id"], values[1]["kyber_prekey"]["id"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_reaction_race_preserves_highest_desired_version(self):
        original = self.envelope()
        self.send(original)
        bodies = [serializers.Envelope(data=self.envelope(kind="reaction", target=original["target_id"], version=i)) for i in (2, 1)]
        for body in bodies:
            body.is_valid(raise_exception=True)
        self.concurrent(lambda i: protocol.accept_operation(self.first, self.first_sid, self.room.pk, bodies[i].validated_data))
        self.assertEqual(MutationHead.objects.get().version, 2)
