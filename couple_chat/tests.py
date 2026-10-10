from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch, AsyncMock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core.management import call_command
from django.db import connections, transaction
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from authentication.models import UserModel, DeviceSession
from authentication.sessions import create_session
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel, SingleConnectionModel
from social.services.relationship_service import invite, transition
from utils.choices import CoupleConnectionStatus as Status
from .models import Room, Change
from .services import dispatch, ensure_room


class Fixtures:
    def setUp(self):
        super().setUp()
        self.first = UserModel.objects.create_user(phone_number="+9779800100001", full_name="First",
            is_phone_verified=True, is_profile_complete=True)
        self.second = UserModel.objects.create_user(phone_number="+9779800100002", full_name="Second",
            is_phone_verified=True, is_profile_complete=True)
        self.third = UserModel.objects.create_user(phone_number="+9779800100003", full_name="Third",
            is_phone_verified=True, is_profile_complete=True)
        self.connection, _ = invite(self.first, self.second.phone_number)

    def accept(self):
        return transition(self.second, self.connection.pk, Status.ACCEPTED,
                          self.first.phone_number, self.second.phone_number)

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + create_session(user)["access"])
        return client


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class RoomTests(Fixtures, APITestCase):
    def test_response_matches_owned_contract(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        room = self.accept()[2]
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/couple-chat-1/schema.json").read_text())
        schema["$ref"] = "#/$defs/state"
        data = self.client_for(self.first).get("/api/couple-chat/v1/state/").data["data"]
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)

    def test_accept_retry_is_one_room_and_one_event(self):
        _, couple, room = self.accept()
        self.assertEqual(self.accept()[2].pk, room.pk)
        self.assertEqual(CoupleModel.objects.count(), 1)
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Change.objects.count(), 1)
        self.assertEqual(list(couple.members.order_by("id")), [self.first, self.second])
        self.assertEqual(room.sequence, 1)

    def test_both_members_can_lookup_but_third_user_cannot(self):
        room = self.accept()[2]
        for user in [self.first, self.second]:
            client = self.client_for(user)
            response = client.get(f"/api/couple-chat/v1/rooms/{room.pk}/")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertFalse(response.data["data"]["can_send"])
            self.assertEqual(response["Cache-Control"], "private, no-store")
        client = self.client_for(self.third)
        self.assertEqual(client.get(f"/api/couple-chat/v1/rooms/{room.pk}/").status_code, 404)
        self.assertIsNone(client.get("/api/couple-chat/v1/state/").data["data"]["room"])

    def test_sender_and_third_user_cannot_accept_or_spoof_path(self):
        from rest_framework.exceptions import APIException
        for user in [self.first, self.third]:
            with self.assertRaises(APIException):
                transition(user, self.connection.pk, Status.ACCEPTED, self.first.phone_number, self.second.phone_number)
        with self.assertRaises(APIException):
            transition(self.second, self.connection.pk, Status.ACCEPTED, self.third.phone_number, self.second.phone_number)
        self.assertEqual(Room.objects.count(), 0)

    def test_blocked_pair_cannot_accept_or_lookup(self):
        room = self.accept()[2]
        SingleConnectionModel.objects.create(sender_number=self.second.phone_number,
            receiver_number=self.first.phone_number, connection_status="BLOCKED")
        self.assertEqual(self.client_for(self.first).get(f"/api/couple-chat/v1/rooms/{room.pk}/").status_code, 404)
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.accept()

    def test_unlink_relink_has_new_relationship_and_no_old_room_access(self):
        _, old_couple, old_room = self.accept()
        transition(self.first, self.connection.pk, Status.BREAKUP, self.first.phone_number, self.second.phone_number)
        transition(self.first, self.connection.pk, Status.BREAKUP, self.first.phone_number, self.second.phone_number)
        self.assertEqual(CoupleMembershipModel.objects.count(), 0)
        old_room.refresh_from_db()
        self.assertIsNotNone(old_room.revoked_at)
        self.assertEqual(old_room.sequence, 2)
        self.assertEqual(self.client_for(self.second).get(f"/api/couple-chat/v1/rooms/{old_room.pk}/").status_code, 404)
        self.connection, _ = invite(self.first, self.second.phone_number)
        _, couple, room = self.accept()
        self.assertNotEqual(couple.pk, old_couple.pk)
        self.assertNotEqual(room.pk, old_room.pk)

    def test_membership_replacement_and_deactivation_deny_access(self):
        _, couple, room = self.accept()
        member = couple.memberships.get(user=self.second)
        position = member.position
        member.delete()
        CoupleMembershipModel.objects.create(couple=couple, user=self.second, position=position)
        self.assertEqual(self.client_for(self.first).get(f"/api/couple-chat/v1/rooms/{room.pk}/").status_code, 404)
        with self.assertRaises(ValueError):
            ensure_room(couple)

    def test_expired_revoked_legacy_and_missing_sessions_are_denied(self):
        self.accept()
        client = self.client_for(self.first)
        DeviceSession.objects.filter(user=self.first).update(revoked_at=timezone.now())
        self.assertEqual(client.get("/api/couple-chat/v1/state/").status_code, 401)
        client = self.client_for(self.second)
        DeviceSession.objects.filter(user=self.second).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(client.get("/api/couple-chat/v1/state/").status_code, 401)
        from rest_framework_simplejwt.tokens import AccessToken
        client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(self.first)))
        self.assertEqual(client.get("/api/couple-chat/v1/state/").status_code, 403)
        self.assertEqual(APIClient().get("/api/couple-chat/v1/state/").status_code, 401)

    def test_rollback_never_dispatches_or_leaves_room(self):
        with patch("couple_chat.services.dispatch") as sent:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    self.accept()
                    transaction.set_rollback(True)
            sent.assert_not_called()
        self.assertEqual(Room.objects.count(), 0)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.connection_status, Status.PENDING)

    def test_dispatch_failure_does_not_undo_commit_and_is_retryable(self):
        with patch("couple_chat.services.get_channel_layer") as channel:
            channel.return_value.group_send = AsyncMock(side_effect=RuntimeError("synthetic failure"))
            with self.captureOnCommitCallbacks(execute=True):
                self.accept()
        change = Change.objects.get()
        self.assertIsNone(change.dispatched_at)
        self.assertTrue(dispatch(change.pk))
        change.refresh_from_db()
        self.assertIsNotNone(change.dispatched_at)

    def test_backfill_dry_run_then_idempotent_apply(self):
        _, couple, room = self.accept()
        room.delete()
        output = StringIO()
        call_command("backfill_couple_chat", dry_run=True, stdout=output)
        self.assertEqual(Room.objects.count(), 0)
        call_command("backfill_couple_chat", stdout=output)
        call_command("backfill_couple_chat", stdout=output)
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Change.objects.count(), 1)

    def test_accept_response_keeps_legacy_fields_and_adds_kmp_data(self):
        client = self.client_for(self.second)
        response = client.put(f"/api/social/connect-couple/{self.connection.pk}/", {
            "sender_number": self.first.phone_number, "receiver_number": self.second.phone_number,
            "connection_status": Status.ACCEPTED}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["couple_chat"], response.data["couple_chat"])
        self.assertEqual(response.data["couple_connection"]["id"], self.connection.pk)
        self.assertEqual(response.data["couple_chat"]["relationship_id"], response.data["couple"]["id"])

    def test_invitation_reads_scoped_and_delete_disallowed(self):
        client = self.client_for(self.third)
        self.assertEqual(client.get(f"/api/social/connect-couple/{self.connection.pk}/").status_code, 404)
        self.assertEqual(client.delete(f"/api/social/connect-couple/{self.connection.pk}/").status_code, 405)

    def test_requester_recovers_accepted_room_after_missing_socket_event(self):
        room = self.accept()[2]
        response = self.client_for(self.first).get("/api/social/have-couple-connection-requested/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["data"]["connection_requested"])
        self.assertEqual(response.data["data"]["connection"]["couple_chat"]["id"], str(room.pk))

    def test_request_listing_route_cannot_bypass_transition_service(self):
        client = self.client_for(self.third)
        path = f"/api/social/couple-connection/{self.connection.pk}/"
        self.assertEqual(client.get(path).status_code, 404)
        self.assertEqual(client.patch(path, {"connection_status": Status.ACCEPTED}, format="json").status_code, 405)
        self.assertEqual(client.delete(path).status_code, 405)
        self.assertEqual(client.post("/api/social/couple-connection/", {}, format="json").status_code, 405)

    def test_account_deletion_revokes_room_without_blocking_account_cleanup(self):
        room = self.accept()[2]
        self.second.delete()
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())
        self.assertIsNone(self.client_for(self.first).get("/api/couple-chat/v1/state/").data["data"]["room"])

    @override_settings(COUPLE_CHAT_PREVIEW_ENABLED=False)
    def test_preview_off_still_creates_room_without_exposing_transport(self):
        self.accept()
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(self.client_for(self.first).get("/api/couple-chat/v1/state/").status_code, 404)


class RaceTests(Fixtures, APITransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_acceptance_race(self):
        barrier = Barrier(2)
        def worker():
            try:
                barrier.wait(timeout=10)
                return self.accept()[2].pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Change.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_inverse_invitation_race(self):
        self.connection.delete()
        barrier = Barrier(2)
        def worker(pair):
            try:
                barrier.wait(timeout=10)
                return invite(*pair)[0].pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, [(self.first, self.second.phone_number), (self.second, self.first.phone_number)]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(CoupleConnectionModel.objects.count(), 1)


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class SocketTests(Fixtures, APITransactionTestCase):
    def test_header_auth_user_hint_and_revoke(self):
        from srisu.asgi import application
        token = create_session(self.first)["access"]
        async def flow():
            socket = WebsocketCommunicator(application, "/ws/couple-chat/v1/updates/",
                headers=[(b"authorization", ("Bearer " + token).encode())])
            self.assertTrue((await socket.connect())[0])
            self.assertEqual(await socket.receive_json_from(), {"version": 1, "type": "state.changed"})
            from channels.db import database_sync_to_async
            await database_sync_to_async(lambda: DeviceSession.objects.filter(user=self.first).update(revoked_at=timezone.now()))()
            from channels.layers import get_channel_layer
            await get_channel_layer().group_send(f"couple_chat.user.{self.first.pk}", {"type": "state.changed"})
            closed = await socket.receive_output()
            self.assertEqual(closed["type"], "websocket.close")
            self.assertEqual(closed["code"], 4401)
            await socket.disconnect()
            url_token = WebsocketCommunicator(application, "/ws/couple-chat/v1/updates/?token=" + token)
            self.assertFalse((await url_token.connect())[0])
            await url_token.disconnect()
        async_to_sync(flow)()

    def test_requester_gets_acceptance_hint_without_room_subscription(self):
        from srisu.asgi import application
        from channels.db import database_sync_to_async
        token = create_session(self.first)["access"]
        stranger_token = create_session(self.third)["access"]
        async def flow():
            sockets = [WebsocketCommunicator(application, "/ws/couple-chat/v1/updates/",
                headers=[(b"authorization", ("Bearer " + value).encode())]) for value in [token, stranger_token]]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
                await socket.receive_json_from()
            await database_sync_to_async(self.accept)()
            self.assertEqual(await sockets[0].receive_json_from(), {"version": 1, "type": "state.changed"})
            self.assertTrue(await sockets[1].receive_nothing(timeout=0.05))
            for socket in sockets:
                await socket.disconnect()
        async_to_sync(flow)()
