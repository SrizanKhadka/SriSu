"""Synthetic Matrix AS/outbox regressions. No Matrix or Firebase network calls."""
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from django.db import connections
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase, APITransactionTestCase

from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping
from authentication.models import DeviceSession
from .models import Delivery, Event, Installation, MatrixPushSuppression, MatrixPushTransaction, Preferences
from .matrix_push import ingest
from .provider import message
from .services import destination
from .tests import Fixtures
from .worker import run_once

TOKEN = "synthetic-matrix-homeserver-token-" + "s" * 32
SETTINGS = dict(NOTIFICATIONS_ENABLED=True, MATRIX_ENABLED=True, MATRIX_CHAT_PUSH_ENABLED=True,
    MATRIX_PUSH_HS_TOKEN=TOKEN, MATRIX_CHAT_PUSH_NOT_BEFORE_MS=0)


class MatrixFixtures(Fixtures):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.bind(version=2, capabilities=["matrix-chat-push-1"]).status_code, 200)
        self.room, _ = ChatRoom.objects.get_or_create(couple=self.couple,
            defaults={"user_one": self.first, "user_two": self.second})
        for user in (self.first, self.second):
            MatrixUserMapping.objects.update_or_create(user=user, defaults={"state": "active",
                "matrix_localpart": f"srisu_user_{user.pk}", "matrix_user_id": f"@srisu_user_{user.pk}:matrix.test"})
        self.mapping, _ = MatrixRoomMapping.objects.update_or_create(chat_room=self.room, defaults={
            "state": "active", "matrix_room_id": "!synthetic:matrix.test", "membership_epoch": 7,
            "room_alias_localpart": f"srisu_chat_{self.room.pk.hex}_e7",
            "member_matrix_localparts": [f"srisu_user_{user.pk}" for user in (self.first, self.second)]})
        self.event = {"type": "m.room.encrypted", "event_id": "$synthetic-message",
            "room_id": self.mapping.matrix_room_id, "sender": self.first.matrix_identity.matrix_user_id,
            "origin_server_ts": int(timezone.now().timestamp() * 1000),
            "content": {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "synthetic-ciphertext-never-retain"}}

    def post_transaction(self, txn="one", events=None, ephemeral=None, **headers):
        return self.client.put(f"/_matrix/app/v1/transactions/{txn}",
            {"events": events if events is not None else [self.event], "ephemeral": ephemeral or []},
            format="json", HTTP_AUTHORIZATION=headers.pop("HTTP_AUTHORIZATION", "Bearer " + TOKEN), **headers)

    def receipt(self, event=None):
        return {"type": "m.receipt", "room_id": self.mapping.matrix_room_id, "content": {
            event or self.event["event_id"]: {"m.read": {self.second.matrix_identity.matrix_user_id: {"ts": 1}}}}}


@override_settings(**SETTINGS)
class MatrixPushTests(MatrixFixtures, APITestCase):
    def test_authenticated_encrypted_message_is_private_and_tap_reauthorizes(self):
        self.assertEqual(self.post_transaction().status_code, 200)
        event = Event.objects.get(); delivery = Delivery.objects.get()
        self.assertEqual(event.recipient_id, self.second.pk)
        self.assertEqual(event.kind, "matrix.message")
        self.assertNotIn("ciphertext", str(event.__dict__))
        self.assertNotIn("synthetic-ciphertext-never-retain", str(event.__dict__))
        wire = message(delivery, audible=True)
        self.assertEqual(set(wire.data), {"schema", "delivery_id", "expires_at"})
        self.assertEqual(wire.android.collapse_key, "chat")
        self.assertEqual(wire.android.notification.channel_id, "srisu_updates")
        url = f"/api/notifications/v1/deliveries/{delivery.pk}/"
        self.assertEqual(self.second_client.get(url).data["data"],
            {"kind": "matrix_conversation", "room_id": str(self.room.pk)})
        self.assertEqual(self.first_client.get(url).status_code, 404)
        self.assertEqual(self.third_client.get(url).status_code, 404)
        self.ready(); sender = Mock(); run_once(sender)
        self.assertEqual(sender.call_count, 1)

    def test_authentication_precedes_parsing_and_rejects_user_or_query_tokens(self):
        for header in ("", "Bearer invalid", self.second_client._credentials["HTTP_AUTHORIZATION"]):
            self.assertEqual(self.post_transaction(HTTP_AUTHORIZATION=header).status_code, 403)
        self.assertEqual(self.client.put("/_matrix/app/v1/transactions/x?access_token=synthetic", {},
            format="json", HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 403)
        self.assertFalse(MatrixPushTransaction.objects.exists())
        self.assertEqual(self.client.post("/_matrix/app/v1/ping", {}, format="json",
            HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 200)

    def test_transaction_and_event_retries_never_create_duplicate_deliveries(self):
        for txn in ("one", "one", "two"):
            self.assertEqual(self.post_transaction(txn).status_code, 200)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(MatrixPushTransaction.objects.count(), 2)

    def test_failed_batch_rolls_back_and_can_retry_without_logging_payload(self):
        with patch("notifications.matrix_push._encrypted_event", side_effect=RuntimeError("synthetic private detail")):
            response = self.post_transaction()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.content.decode())
        self.assertFalse(MatrixPushTransaction.objects.exists())
        self.assertEqual(self.post_transaction().status_code, 200)
        self.assertEqual(Delivery.objects.count(), 1)

    def test_only_new_supported_encrypted_timeline_messages_are_eligible(self):
        variants = [{"type": "m.room.message", "content": {"body": "synthetic plaintext"}},
            {"type": "m.reaction"}, {"state_key": ""}, {"room_id": "!unknown:matrix.test"},
            {"sender": self.third.matrix_identity.matrix_user_id if hasattr(self.third, "matrix_identity") else "@stranger:matrix.test"},
            {"origin_server_ts": int((timezone.now() - timedelta(minutes=11)).timestamp() * 1000)},
            {"origin_server_ts": True}, {"origin_server_ts": 999999999999999},
            {"unsigned": {"redacted_because": {}}},
            {"content": {**self.event["content"], "m.relates_to": {"rel_type": "m.replace"}}},
            {"content": {"algorithm": "unsupported", "ciphertext": "synthetic"}}]
        for index, changes in enumerate(variants):
            self.assertEqual(self.post_transaction(str(index), [{**self.event, **changes}]).status_code, 200)
        self.assertFalse(Event.objects.exists())

    def test_preferences_mute_and_kill_switch_have_no_backlog(self):
        for values in ({"push": False}, {"chat": False}, {"mute_forever": True},
                       {"mute_until": timezone.now() + timedelta(hours=1)}):
            Preferences.objects.update_or_create(user=self.second, defaults={"push": True, "chat": True,
                "mute_forever": False, "mute_until": None, **values})
            self.event["event_id"] += "x"
            self.post_transaction(self.event["event_id"])
        self.assertFalse(Delivery.objects.exists())
        Preferences.objects.all().delete()
        self.event["event_id"] = "$while-disabled"
        with override_settings(MATRIX_CHAT_PUSH_ENABLED=False):
            self.post_transaction("disabled")
        self.post_transaction("disabled")
        self.assertFalse(Delivery.objects.exists())

    def test_legacy_and_apple_bindings_are_not_backfilled_on_upgrade(self):
        Installation.objects.update(capabilities=[])
        self.post_transaction()
        self.assertFalse(Delivery.objects.exists())
        self.bind(version=3, capabilities=["matrix-chat-push-1"])
        self.post_transaction("retry")
        self.assertFalse(Delivery.objects.exists())
        Installation.objects.update(platform="ios")
        self.event["event_id"] += "next"
        self.post_transaction("apple")
        self.assertFalse(Delivery.objects.exists())

    def test_binding_capability_change_requires_new_version(self):
        self.assertEqual(self.bind(version=2, capabilities=[]).status_code, 409)
        self.assertEqual(self.bind(version=3, capabilities=[]).status_code, 200)

    def test_send_and_tap_recheck_epoch_membership_and_session(self):
        self.post_transaction(); event = Event.objects.get(); self.ready()
        MatrixRoomMapping.objects.filter(pk=self.mapping.pk).update(membership_epoch=8)
        with self.assertRaises(NotFound): destination(event, self.second, self.second_sid)
        sender = Mock(); run_once(sender); sender.assert_not_called()
        MatrixRoomMapping.objects.filter(pk=self.mapping.pk).update(membership_epoch=7)
        member = self.couple.memberships.get(user=self.second)
        member.ended_at = timezone.now(); member.save()
        with self.assertRaises(NotFound): destination(event, self.second, self.second_sid)

    def test_muted_revoked_or_rebound_device_is_rechecked_at_dispatch(self):
        self.post_transaction(); self.ready()
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        sender = Mock(); run_once(sender); sender.assert_not_called()

    def test_current_blocked_relationship_does_not_emit(self):
        from social.models import SingleConnectionModel
        SingleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number, connection_status="BLOCKED")
        self.post_transaction()
        self.assertFalse(Event.objects.exists())

    def test_late_redaction_and_preference_change_suppress_queued_alerts(self):
        self.post_transaction()
        self.post_transaction("redacted", [{"type": "m.room.redaction", "room_id": self.event["room_id"],
            "content": {"redacts": self.event["event_id"]}}])
        self.assertEqual(Delivery.objects.get().outcome, "redacted")
        with self.assertRaises(NotFound): destination(Event.objects.get(), self.second, self.second_sid)
        self.event["event_id"] = "$muted-after-enqueue"
        self.post_transaction("pending")
        Preferences.objects.create(user=self.second, chat=False)
        self.ready(); sender = Mock(); run_once(sender); sender.assert_not_called()

    def test_rollout_cutoff_and_replaced_mapping_cannot_send_old_history(self):
        with override_settings(MATRIX_CHAT_PUSH_NOT_BEFORE_MS=self.event["origin_server_ts"] + 1):
            self.post_transaction("before-enable")
        self.assertFalse(Event.objects.exists())
        MatrixRoomMapping.objects.filter(pk=self.mapping.pk).update(replacement_pending=True)
        self.post_transaction("replacing")
        self.assertFalse(Event.objects.exists())
        MatrixRoomMapping.objects.filter(pk=self.mapping.pk).update(replacement_pending=False, pending_remote_revocations=["!old:matrix.test"])
        self.post_transaction("pending-remote-revoke")
        self.assertFalse(Event.objects.exists())
        MatrixRoomMapping.objects.filter(pk=self.mapping.pk).update(state="revoke_pending")
        self.post_transaction("revoking")
        self.assertFalse(Event.objects.exists())

    def test_receipt_suppresses_pending_delivery_but_tap_can_still_open_chat(self):
        self.post_transaction()
        self.post_transaction("read", [], [self.receipt()])
        self.assertEqual(Delivery.objects.get().outcome, "read")
        self.assertEqual(destination(Event.objects.get(), self.second, self.second_sid)["kind"], "matrix_conversation")
        self.ready(); sender = Mock(); run_once(sender); sender.assert_not_called()

    def test_receipt_and_redaction_before_message_fence_late_arrival(self):
        self.post_transaction("read-first", [], [self.receipt()])
        self.post_transaction()
        self.assertFalse(Delivery.objects.exists())
        self.event["event_id"] = "$redacted-message"
        redaction = {"type": "m.room.redaction", "room_id": self.event["room_id"],
            "content": {"redacts": self.event["event_id"]}}
        self.post_transaction("redact-first", [redaction])
        self.post_transaction("late")
        self.assertFalse(Delivery.objects.exists())
        with self.assertRaises(NotFound):
            destination(Event.objects.get(matrix_event_id=self.event["event_id"]), self.second, self.second_sid)

    def test_existing_sound_budget_is_shared_with_activity_alerts(self):
        self.post_transaction(); self.ready(); sender = Mock(); run_once(sender)
        self.assertTrue(sender.call_args.kwargs["audible"])
        self.event["event_id"] = "$second-message"
        self.post_transaction("second"); self.ready(); run_once(sender)
        self.assertFalse(sender.call_args.kwargs["audible"])

    def test_malformed_bounded_request_has_no_ack_or_partial_writes(self):
        response = self.client.put("/_matrix/app/v1/transactions/bad", {"events": {}},
            format="json", HTTP_AUTHORIZATION="Bearer " + TOKEN)
        self.assertEqual(response.status_code, 400)
        with patch("notifications.matrix_api.MAX_BODY", 10):
            self.assertEqual(self.post_transaction().status_code, 413)
        self.assertFalse(MatrixPushTransaction.objects.exists())


@override_settings(**SETTINGS)
class MatrixPushConcurrencyTests(MatrixFixtures, APITransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_transaction_retry_creates_one_delivery(self):
        barrier = Barrier(2)
        def attempt():
            connections.close_all()
            try:
                barrier.wait()
                ingest("same", {"events": [deepcopy(self.event)]})
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(MatrixPushTransaction.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Delivery.objects.count(), 1)
