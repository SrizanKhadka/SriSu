import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import timedelta
from io import BytesIO, StringIO
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from PIL import Image

from authentication.models import DeviceSession, UserModel
from chat.models import (
    ChatChange,
    ChatOperation,
    ChatOutbox,
    ChatRoom,
    EncryptedAttachment,
    MediaModel,
    MessageDeletion,
    MessageModel,
)
from chat.services.message_service import (
    DeleteMessageInput,
    EditMessageInput,
    SendMessageInput,
    delete_message,
    edit_message,
    send_message,
)
from chat.services.outbox import dispatch_pending
from chat.services.reaction_service import react_to_message
from chat.services.receipt_service import mark_messages_delivered, mark_messages_read
from chat.services.typing_service import set_typing_status
from chat.services.v2 import ChatV2NotFound, ChatV2Validation, send_encrypted_message
from chat.websocket.exceptions import (
    ChatRoomNotFoundError,
    InvalidMessagePayloadError,
    PermissionDeniedError,
)
from social.models import CoupleConnectionModel
from social.services.relationship_service import (
    RelationshipConflict,
    accept_connection,
    create_connection_request,
    end_connection,
)


def make_user(index):
    return UserModel.objects.create_user(
        phone_number=f"+15005559{index:03}",
        full_name=f"Synthetic Chat {index}",
        is_phone_verified=True,
        is_profile_complete=True,
    )


def make_relationship(first, second):
    connection_row = CoupleConnectionModel.objects.create(
        sender_number=first.phone_number,
        receiver_number=second.phone_number,
        connection_status="PENDING",
    )
    return accept_connection(connection_id=connection_row.pk, actor=second)


def synthetic_jpeg(*, size=(8, 8), metadata=False, trailer=b""):
    buffer = BytesIO()
    image = Image.new("RGB", size, "#336699")
    exif = Image.Exif()
    if metadata:
        exif[0x010E] = "private synthetic metadata"
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue() + trailer


class ChatV2Tests(TestCase):
    def setUp(self):
        self.first, self.second, self.outsider, self.fourth = [make_user(i) for i in range(4)]
        relationship = make_relationship(self.first, self.second)
        self.connection = relationship.connection
        self.room = relationship.chat_room
        self.other_room = make_relationship(self.outsider, self.fourth).chat_room
        self.client = APIClient()
        self.client.force_authenticate(self.first)
        self.messages_url = f"/api/chat/v2/rooms/{self.room.pk}/messages/"
        self.changes_url = f"/api/chat/v2/rooms/{self.room.pk}/changes/"
        self.operations_url = f"/api/chat/v2/rooms/{self.room.pk}/operations/"
        self.receipts_url = f"/api/chat/v2/rooms/{self.room.pk}/receipts/"

    def enabled(self):
        return override_settings(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="test_adapter",
            CHAT_V2_TEST_ADAPTER_ENABLED=True,
            CHAT_V2_ALLOWED_USER_IDS={self.first.id, self.second.id},
            CHAT_V2_REQUIRE_DEVICE_SESSION=False,
        )

    def send(self, operation_id=None):
        return self.client.post(
            self.messages_url,
            {
                "operation_id": str(operation_id or uuid4()),
                "content_kind": "text",
                "envelope": {"ciphertext": "synthetic-opaque-value", "version": 0},
            },
            format="json",
        )

    def test_capabilities_match_fixed_client_contract_and_are_account_scoped(self):
        response = self.client.get("/api/chat/v2/capabilities/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], {
            "api_version": 2,
            "encrypted_writes_enabled": False,
            "protocol_status": "adapter_required",
            "requires_device_session": True,
            "supported_kinds": ["text"],
            "max_message_bytes": 4000,
            "max_envelope_bytes": 64 * 1024,
            "max_attachment_bytes": 15 * 1024 * 1024,
        })
        self.assertEqual(self.send().status_code, 503)
        with override_settings(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="ready",
            CHAT_V2_ALLOWED_USER_IDS={self.first.id, self.second.id},
            CHAT_V2_REQUIRE_DEVICE_SESSION=False,
        ):
            self.assertTrue(self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"])
            self.client.force_authenticate(self.outsider)
            self.assertFalse(self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"])

    def test_capabilities_require_a_current_device_session_when_configured(self):
        enabled = dict(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="ready",
            CHAT_V2_ALLOWED_USER_IDS={self.first.id, self.second.id},
            CHAT_V2_REQUIRE_DEVICE_SESSION=True,
        )
        with override_settings(**enabled):
            self.assertFalse(
                self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"]
            )
            session = DeviceSession.objects.create(
                user=self.first,
                expires_at=timezone.now() + timedelta(hours=1),
            )
            self.client.force_authenticate(
                self.first,
                token={"sid": str(session.pk)},
            )
            self.assertTrue(
                self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"]
            )
            session.revoked_at = timezone.now()
            session.save(update_fields=["revoked_at"])
            self.assertFalse(
                self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"]
            )
            session.revoked_at = None
            session.expires_at = timezone.now() - timedelta(microseconds=1)
            session.save(update_fields=["revoked_at", "expires_at"])
            self.assertFalse(
                self.client.get("/api/chat/v2/capabilities/").data["data"]["encrypted_writes_enabled"]
            )

    def test_send_is_idempotent_ordered_and_never_returns_plaintext(self):
        operation_id = uuid4()
        with self.enabled():
            created = self.send(operation_id)
            replay = self.send(operation_id)
            conflict = self.client.post(
                self.messages_url,
                {
                    "operation_id": str(operation_id),
                    "content_kind": "text",
                    "envelope": {"ciphertext": "different"},
                },
                format="json",
            )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data["data"]["replayed"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(MessageModel.objects.filter(chat_room=self.room).count(), 1)
        self.assertEqual(ChatOperation.objects.count(), 1)
        self.assertEqual(ChatChange.objects.count(), 1)
        self.assertEqual(ChatOutbox.objects.count(), 1)
        dto = created.data["data"]["message"]
        self.assertEqual(dto["operation_id"], str(operation_id))
        self.assertNotIn("text", dto)
        self.assertNotIn("media_url", dto)
        message = MessageModel.objects.get()
        self.assertIsNone(message.text)
        self.assertFalse(message.legacy_plaintext)
        self.assertEqual(message.sequence, 1)
        self.room.refresh_from_db()
        self.assertIsNotNone(self.room.encrypted_v2_started_at)

    def test_room_send_requires_both_participants_in_pilot(self):
        with override_settings(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="test_adapter",
            CHAT_V2_TEST_ADAPTER_ENABLED=True,
            CHAT_V2_ALLOWED_USER_IDS={self.first.id},
            CHAT_V2_REQUIRE_DEVICE_SESSION=False,
        ):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(MessageModel.objects.filter(chat_room=self.room).exists())

    def test_legacy_protocol_cannot_read_or_mutate_v2_messages_or_downgrade_room(self):
        with self.enabled():
            created = self.send()
        message = MessageModel.objects.get(pk=created.data["data"]["message"]["id"])

        history = self.client.get(f"/api/chat/rooms/{self.room.pk}/messages/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data["data"]["messages"], [])
        rooms = self.client.get("/api/chat/rooms/")
        preview = next(
            item
            for item in rooms.data["data"]["chat_rooms"]
            if item["id"] == str(self.room.pk)
        )
        self.assertIsNone(preview["last_message"])
        legacy_upload = self.client.post(
            "/api/chat/media-upload/",
            {
                "chat_room_id": str(self.room.pk),
                "file": SimpleUploadedFile(
                    "legacy.bin",
                    b"plaintext-media-path",
                    content_type="application/octet-stream",
                ),
            },
            format="multipart",
        )
        self.assertEqual(legacy_upload.status_code, 409)

        with self.assertRaises(InvalidMessagePayloadError):
            edit_message(
                user=self.first,
                payload=EditMessageInput(message_id=message.pk, text="plaintext downgrade"),
            )
        with self.assertRaises(InvalidMessagePayloadError):
            delete_message(
                user=self.first,
                payload=DeleteMessageInput(
                    message_id=message.pk,
                    delete_option="DELETE_FOR_EVERYONE",
                ),
            )
        with self.assertRaises(InvalidMessagePayloadError):
            react_to_message(user=self.second, message_id=message.pk, reaction="Love")
        with self.assertRaises(InvalidMessagePayloadError):
            mark_messages_delivered(user=self.second, chat_room_id=str(self.room.pk))
        with self.assertRaises(InvalidMessagePayloadError):
            mark_messages_read(user=self.second, chat_room_id=str(self.room.pk))
        with self.assertRaises(InvalidMessagePayloadError):
            set_typing_status(
                user=self.first,
                chat_room_id=str(self.room.pk),
                is_typing=True,
            )
        with self.assertRaises(InvalidMessagePayloadError):
            send_message(
                user=self.first,
                payload=SendMessageInput(
                    chat_room_id=str(self.room.pk),
                    text="plaintext downgrade",
                ),
            )

        message.refresh_from_db()
        self.assertIsNone(message.text)
        self.assertEqual(message.revision, 1)
        self.assertEqual(message.encrypted_envelope, {"ciphertext": "synthetic-opaque-value", "version": 0})

    def test_ambiguous_legacy_private_deletion_is_quarantined_and_preflighted(self):
        ambiguous = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="must remain quarantined",
            delete_option="DELETE_FOR_ME",
            sequence=1,
        )
        self.room.last_message = ambiguous
        self.room.last_sequence = 1
        self.room.save(update_fields=["last_message", "last_sequence", "updated_at"])

        for viewer in (self.first, self.second):
            self.client.force_authenticate(viewer)
            legacy = self.client.get(f"/api/chat/rooms/{self.room.pk}/messages/")
            v2 = self.client.get(self.messages_url)
            rooms = self.client.get("/api/chat/rooms/")
            self.assertEqual(legacy.data["data"]["messages"], [])
            self.assertEqual(v2.data["data"]["messages"], [])
            self.assertIsNone(rooms.data["data"]["chat_rooms"][0]["last_message"])
            self.assertNotIn("must remain quarantined", str(legacy.data))
            self.assertNotIn("must remain quarantined", str(rooms.data))

        with self.assertRaises(CommandError):
            call_command("preflight_relationship_lifecycle", stdout=StringIO())
        MessageDeletion.objects.create(
            message=ambiguous,
            user=self.first,
            delete_option="DELETE_FOR_ME",
        )
        call_command("preflight_relationship_lifecycle", stdout=StringIO())

    def test_v2_reaction_rejects_globally_tombstoned_message(self):
        with self.enabled():
            message = self.send().data["data"]["message"]
            deleted = self.client.post(
                self.operations_url,
                {
                    "operation_id": str(uuid4()),
                    "action": "delete_for_everyone",
                    "message_id": message["id"],
                    "expected_revision": message["revision"],
                },
                format="json",
            )
            self.assertEqual(deleted.status_code, 200)
            self.client.force_authenticate(self.second)
            reaction = self.client.post(
                self.operations_url,
                {
                    "operation_id": str(uuid4()),
                    "action": "set_reaction",
                    "message_id": message["id"],
                    "envelope": {"ciphertext": "too-late"},
                },
                format="json",
            )
        self.assertEqual(reaction.status_code, 404)

    def test_pages_conceal_legacy_plaintext_and_unauthorized_rooms(self):
        legacy = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="must-not-leave-server-via-v2",
            sequence=1,
        )
        self.room.last_sequence = 1
        self.room.save(update_fields=["last_sequence"])
        response = self.client.get(self.messages_url)
        self.assertEqual(response.status_code, 200)
        dto = response.data["data"]["messages"][0]
        self.assertIsNone(dto["operation_id"])
        self.assertTrue(dto["legacy_plaintext"])
        self.assertIsNone(dto["envelope"])
        self.assertNotIn(legacy.text, str(response.data))
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.messages_url).status_code, 404)
        self.assertEqual(self.client.get(self.changes_url).status_code, 404)

    def test_history_and_changes_are_bounded_to_the_captured_high_watermark(self):
        from chat.services.v2 import (
            message_page as real_message_page,
            visible_changes_window as real_visible_changes_window,
        )

        with self.enabled():
            first = self.send().data["data"]["message"]

            def race_message_page(**kwargs):
                send_encrypted_message(
                    room_id=self.room.pk,
                    user=self.first,
                    operation_id=uuid4(),
                    content_kind="text",
                    envelope={"ciphertext": "created-after-message-snapshot"},
                )
                return real_message_page(**kwargs)

            with patch("chat.api.v2.message_page", side_effect=race_message_page):
                history = self.client.get(self.messages_url).data["data"]

            self.assertEqual(history["high_watermark"], first["sequence"])
            self.assertEqual(
                [item["sequence"] for item in history["messages"]],
                [first["sequence"]],
            )

            before_changes = ChatRoom.objects.get(pk=self.room.pk).last_sequence

            def race_changes_window(**kwargs):
                send_encrypted_message(
                    room_id=self.room.pk,
                    user=self.first,
                    operation_id=uuid4(),
                    content_kind="text",
                    envelope={"ciphertext": "created-after-change-snapshot"},
                )
                return real_visible_changes_window(**kwargs)

            with patch(
                "chat.api.v2.visible_changes_window",
                side_effect=race_changes_window,
            ):
                changes = self.client.get(self.changes_url).data["data"]

        self.assertEqual(changes["high_watermark"], before_changes)
        self.assertTrue(
            all(item["sequence"] <= changes["high_watermark"] for item in changes["changes"])
        )
        self.assertLessEqual(changes["next_after_sequence"], changes["high_watermark"])

    def test_private_change_gaps_advance_by_highest_scanned_sequence(self):
        with self.enabled():
            message = self.send().data["data"]["message"]
            hidden = self.client.post(
                self.operations_url,
                {
                    "operation_id": str(uuid4()),
                    "action": "delete_for_me",
                    "message_id": message["id"],
                },
                format="json",
            )
            self.assertEqual(hidden.status_code, 200)
            self.client.force_authenticate(self.second)
            send_encrypted_message(
                room_id=self.room.pk,
                user=self.second,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "partner"},
            )
        first_page = self.client.get(self.changes_url, {"after_sequence": 1, "limit": 1}).data["data"]
        self.assertEqual(first_page["changes"], [])
        self.assertEqual(first_page["next_after_sequence"], 2)
        self.assertTrue(first_page["has_more"])
        second_page = self.client.get(self.changes_url, {"after_sequence": 2, "limit": 1}).data["data"]
        self.assertEqual([item["sequence"] for item in second_page["changes"]], [3])

    def test_receipts_are_monotonic_and_reactions_are_desired_state(self):
        with self.enabled():
            message = self.send().data["data"]["message"]
            self.client.force_authenticate(self.second)
            receipt_operation = uuid4()
            receipt = self.client.put(self.receipts_url, {
                "operation_id": str(receipt_operation),
                "delivered_through": message["sequence"],
                "read_through": message["sequence"],
            }, format="json")
            replay = self.client.put(self.receipts_url, {
                "operation_id": str(receipt_operation),
                "delivered_through": message["sequence"],
                "read_through": message["sequence"],
            }, format="json")
            lower = self.client.put(self.receipts_url, {
                "operation_id": str(uuid4()),
                "delivered_through": 0,
                "read_through": 0,
            }, format="json")
            reaction = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()),
                "action": "set_reaction",
                "message_id": message["id"],
                "envelope": {"ciphertext": "reaction-one"},
            }, format="json")
            removal = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()),
                "action": "set_reaction",
                "message_id": message["id"],
                "envelope": None,
            }, format="json")
            self.room.refresh_from_db()
            high_after_non_message_changes = self.room.last_sequence
            normalized = self.client.put(self.receipts_url, {
                "operation_id": str(uuid4()),
                "delivered_through": high_after_non_message_changes,
                "read_through": high_after_non_message_changes,
            }, format="json")
        self.assertEqual(receipt.status_code, 200)
        self.assertTrue(replay.data["data"]["replayed"])
        self.assertEqual(lower.data["data"]["read_through"], message["sequence"])
        self.assertEqual(reaction.data["data"]["message"]["reactions"][0]["envelope"], {"ciphertext": "reaction-one"})
        self.assertTrue(removal.data["data"]["message"]["reactions"][0]["is_removed"])
        self.assertEqual(normalized.data["data"]["read_through"], message["sequence"])
        self.assertIsNone(normalized.data["data"]["change_sequence"])
        self.room.refresh_from_db()
        self.assertEqual(self.room.last_sequence, high_after_non_message_changes)

    def test_edit_and_delete_windows_are_inclusive_then_close(self):
        fixed_now = timezone.now()
        with self.enabled(), patch("chat.services.v2.timezone.now", return_value=fixed_now):
            first = send_encrypted_message(
                room_id=self.room.pk,
                user=self.first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "first"},
            ).message
            MessageModel.objects.filter(pk=first.pk).update(timestamp=fixed_now - timedelta(minutes=15))
            allowed = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()), "action": "edit", "message_id": first.pk,
                "expected_revision": 1, "envelope": {"ciphertext": "edited"},
            }, format="json")
            self.assertEqual(allowed.status_code, 200)

            second = send_encrypted_message(
                room_id=self.room.pk,
                user=self.first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "second"},
            ).message
            MessageModel.objects.filter(pk=second.pk).update(timestamp=fixed_now - timedelta(minutes=15, microseconds=1))
            denied = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()), "action": "edit", "message_id": second.pk,
                "expected_revision": 1, "envelope": {"ciphertext": "late"},
            }, format="json")
            self.assertEqual(denied.status_code, 409)

            third = send_encrypted_message(
                room_id=self.room.pk,
                user=self.first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "third"},
            ).message
            MessageModel.objects.filter(pk=third.pk).update(timestamp=fixed_now - timedelta(hours=48))
            allowed_delete = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()), "action": "delete_for_everyone",
                "message_id": third.pk, "expected_revision": 1,
            }, format="json")
            self.assertEqual(allowed_delete.status_code, 200)

            fourth = send_encrypted_message(
                room_id=self.room.pk,
                user=self.first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "fourth"},
            ).message
            MessageModel.objects.filter(pk=fourth.pk).update(timestamp=fixed_now - timedelta(hours=48, microseconds=1))
            denied_delete = self.client.post(self.operations_url, {
                "operation_id": str(uuid4()), "action": "delete_for_everyone",
                "message_id": fourth.pk, "expected_revision": 1,
            }, format="json")
            self.assertEqual(denied_delete.status_code, 409)

    def test_relationship_relink_gets_a_new_room_and_revokes_old_history(self):
        old_room = self.room
        MessageModel.objects.create(
            chat_room=old_room,
            couple=old_room.couple,
            sender=self.first,
            receiver=self.second,
            text="preserved legacy history",
        )
        end_connection(connection_id=self.connection.pk, actor=self.first)
        self.assertEqual(self.client.get(self.messages_url).status_code, 404)
        relink = make_relationship(self.first, self.second)
        self.assertNotEqual(relink.chat_room.pk, old_room.pk)
        self.assertTrue(MessageModel.objects.filter(chat_room=old_room).exists())
        self.assertEqual(ChatRoom.objects.filter(couple=relink.couple).count(), 1)

    def test_legacy_media_is_owned_scoped_and_delivered_only_from_visible_room(self):
        upload = SimpleUploadedFile(
            "photo.jpg",
            synthetic_jpeg(),
            content_type="image/jpeg",
        )
        media = MediaModel.objects.create(file=upload, owner=self.first)
        with self.assertRaises(PermissionDeniedError):
            send_message(
                user=self.outsider,
                payload=SendMessageInput(
                    chat_room_id=str(self.other_room.pk),
                    text="attempt",
                    media_ids=[media.pk],
                ),
            )
        message, _ = send_message(
            user=self.first,
            payload=SendMessageInput(
                chat_room_id=str(self.room.pk),
                text="authorized",
                media_ids=[media.pk],
            ),
        )
        url = media.file.url
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        ownerless = MediaModel.objects.create(
            file=SimpleUploadedFile("orphan.bin", b"orphan", content_type="application/octet-stream")
        )
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.get(ownerless.file.url).status_code, 404)
        self.assertTrue(MessageModel.objects.filter(pk=message.pk).exists())

    def test_legacy_upload_sniffs_reencodes_and_returns_only_guarded_urls(self):
        marker = b"<script>private-polyglot-marker</script>"
        response = self.client.post(
            "/api/chat/media-upload/",
            {
                "chat_room_id": str(self.room.pk),
                "file": SimpleUploadedFile(
                    "misleading.html",
                    synthetic_jpeg(metadata=True, trailer=marker),
                    content_type="text/html",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        dto = response.data["data"]["media"][0]
        self.assertEqual(set(dto), {"id", "file_url", "uploaded_at"})
        self.assertIn("/media/chats/media/", dto["file_url"])

        media = MediaModel.objects.get(pk=dto["id"])
        with media.file.open("rb") as stored:
            normalized = stored.read()
        self.assertNotIn(marker, normalized)
        with Image.open(BytesIO(normalized)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(len(image.getexif()), 0)
            self.assertLessEqual(max(image.size), 2000)

        spoofed = self.client.post(
            "/api/chat/media-upload/",
            {
                "chat_room_id": str(self.room.pk),
                "file": SimpleUploadedFile(
                    "spoofed.jpg",
                    b"<html><script>not-an-image</script></html>",
                    content_type="image/jpeg",
                ),
            },
            format="multipart",
        )
        self.assertEqual(spoofed.status_code, 400)
        self.assertIn("file_0", str(spoofed.data))

        with patch("utils.image_processing.MAX_PIXELS", 1):
            oversized_dimensions = self.client.post(
                "/api/chat/media-upload/",
                {
                    "chat_room_id": str(self.room.pk),
                    "file": SimpleUploadedFile(
                        "too-many-pixels.png",
                        synthetic_jpeg(size=(2, 2)),
                        content_type="image/png",
                    ),
                },
                format="multipart",
            )
        self.assertEqual(oversized_dimensions.status_code, 400)

    def test_legacy_couple_send_rejects_direct_media_and_sticker_urls(self):
        for field in ("media_url", "sticker_url"):
            with self.assertRaises(InvalidMessagePayloadError):
                send_message(
                    user=self.first,
                    payload=SendMessageInput(
                        chat_room_id=str(self.room.pk),
                        text="not enough to authorize a direct origin",
                        **{field: "https://attacker.invalid/content"},
                    ),
                )

    def test_delete_for_everyone_removes_direct_legacy_blob_after_commit(self):
        message = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="legacy photo",
            media=SimpleUploadedFile(
                "direct.jpg",
                synthetic_jpeg(),
                content_type="image/jpeg",
            ),
        )
        storage = message.media.storage
        storage_name = message.media.name
        self.assertTrue(storage.exists(storage_name))

        with self.captureOnCommitCallbacks(execute=True):
            delete_message(
                user=self.first,
                payload=DeleteMessageInput(
                    message_id=message.pk,
                    delete_option="DELETE_FOR_EVERYONE",
                ),
            )

        message.refresh_from_db()
        self.assertFalse(message.media)
        self.assertFalse(storage.exists(storage_name))

    def test_failed_direct_media_delete_keeps_a_durable_cleanup_reference(self):
        message = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="legacy photo",
            media=SimpleUploadedFile(
                "retry.jpg",
                synthetic_jpeg(),
                content_type="image/jpeg",
            ),
        )
        storage = message.media.storage
        storage_name = message.media.name
        with patch.object(storage, "delete", side_effect=OSError("offline")):
            with self.captureOnCommitCallbacks(execute=True):
                delete_message(
                    user=self.first,
                    payload=DeleteMessageInput(
                        message_id=message.pk,
                        delete_option="DELETE_FOR_EVERYONE",
                    ),
                )

        message.refresh_from_db()
        self.assertEqual(message.media.name, storage_name)
        self.assertTrue(storage.exists(storage_name))
        call_command("cleanup_chat_media", limit=10, stdout=StringIO())
        message.refresh_from_db()
        self.assertFalse(message.media)
        self.assertFalse(storage.exists(storage_name))

    def test_legacy_upload_cleanup_removes_partial_and_abandoned_blobs(self):
        storage = MediaModel._meta.get_field("file").storage
        stored_names = []
        original_create = MediaModel.objects.create
        calls = 0

        def fail_second_create(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic database failure")
            row = original_create(**kwargs)
            stored_names.append(row.file.name)
            return row

        with patch.object(MediaModel.objects, "create", side_effect=fail_second_create):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/chat/media-upload/",
                    {
                        "chat_room_id": str(self.room.pk),
                        "file": [
                            SimpleUploadedFile("first.jpg", synthetic_jpeg(), content_type="image/jpeg"),
                            SimpleUploadedFile("second.jpg", synthetic_jpeg(), content_type="image/jpeg"),
                        ],
                    },
                    format="multipart",
                )
        self.assertEqual(MediaModel.objects.count(), 0)
        self.assertTrue(stored_names)
        self.assertTrue(all(not storage.exists(name) for name in stored_names))

        stale = MediaModel.objects.create(
            file=SimpleUploadedFile("stale.jpg", synthetic_jpeg(), content_type="image/jpeg"),
            owner=self.first,
        )
        stale_name = stale.file.name
        MediaModel.objects.filter(pk=stale.pk).update(
            uploaded_at=timezone.now() - timedelta(days=2)
        )
        encrypted = EncryptedAttachment.objects.create(
            chat_room=self.room,
            owner=self.first,
            operation_id=uuid4(),
            ciphertext=SimpleUploadedFile(
                "stale.bin",
                b"opaque-ciphertext",
                content_type="application/octet-stream",
            ),
            ciphertext_sha256=hashlib.sha256(b"opaque-ciphertext").hexdigest(),
            ciphertext_size=len(b"opaque-ciphertext"),
            state=EncryptedAttachment.State.CANCELLED,
            expires_at=timezone.now() + timedelta(days=1),
        )
        encrypted_name = encrypted.ciphertext.name
        durable_change = ChatChange.objects.create(
            chat_room=self.room,
            sequence=1,
            kind="synthetic_cleanup_test",
            actor=self.first,
        )
        published_outbox = ChatOutbox.objects.create(
            change=durable_change,
            state=ChatOutbox.State.PUBLISHED,
            published_at=timezone.now() - timedelta(days=8),
        )
        call_command("cleanup_chat_media", limit=10, stdout=StringIO())
        self.assertFalse(MediaModel.objects.filter(pk=stale.pk).exists())
        self.assertFalse(EncryptedAttachment.objects.filter(pk=encrypted.pk).exists())
        self.assertFalse(storage.exists(stale_name))
        encrypted_storage = EncryptedAttachment._meta.get_field("ciphertext").storage
        self.assertFalse(encrypted_storage.exists(encrypted_name))
        self.assertFalse(ChatOutbox.objects.filter(pk=published_outbox.pk).exists())
        self.assertTrue(ChatChange.objects.filter(pk=durable_change.pk).exists())

    def test_legacy_upload_has_a_dedicated_rate_budget(self):
        cache.clear()
        try:
            with patch.dict(
                ScopedRateThrottle.THROTTLE_RATES,
                {"chat_media_uploads": "1/min"},
                clear=False,
            ):
                first = self.client.post(
                    "/api/chat/media-upload/",
                    {
                        "chat_room_id": str(self.room.pk),
                        "file": SimpleUploadedFile(
                            "first.jpg", synthetic_jpeg(), content_type="image/jpeg"
                        ),
                    },
                    format="multipart",
                )
                throttled = self.client.post(
                    "/api/chat/media-upload/",
                    {
                        "chat_room_id": str(self.room.pk),
                        "file": SimpleUploadedFile(
                            "second.jpg", synthetic_jpeg(), content_type="image/jpeg"
                        ),
                    },
                    format="multipart",
                )
        finally:
            cache.clear()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(throttled.status_code, 429)

    def test_text_pilot_does_not_enable_attachment_stage_or_claim(self):
        body = b"synthetic-ciphertext"
        attachment_url = f"/api/chat/v2/rooms/{self.room.pk}/attachments/"
        with self.enabled():
            staged = self.client.post(
                attachment_url,
                {
                    "operation_id": str(uuid4()),
                    "ciphertext_sha256": hashlib.sha256(body).hexdigest(),
                    "ciphertext": SimpleUploadedFile(
                        "ciphertext.bin",
                        body,
                        content_type="application/octet-stream",
                    ),
                },
                format="multipart",
            )
            claimed = self.client.post(
                self.messages_url,
                {
                    "operation_id": str(uuid4()),
                    "content_kind": "text",
                    "envelope": {"ciphertext": "synthetic-opaque-value"},
                    "attachment_ids": [str(uuid4())],
                },
                format="json",
            )
        self.assertEqual(staged.status_code, 503)
        self.assertEqual(claimed.status_code, 503)

    def test_v2_scoped_throttles_apply_and_attachment_scope_is_separate(self):
        configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("chat_v2_reads", configured)
        self.assertIn("chat_v2_writes", configured)
        self.assertIn("chat_v2_attachments", configured)

        cache.clear()
        try:
            with patch.dict(
                ScopedRateThrottle.THROTTLE_RATES,
                {"chat_v2_reads": "1/min"},
                clear=False,
            ):
                first = self.client.get("/api/chat/v2/capabilities/")
                throttled = self.client.get("/api/chat/v2/capabilities/")
        finally:
            cache.clear()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(throttled.status_code, 429)

    def test_backfill_is_resumable_and_refuses_unsafe_mixed_chronology(self):
        older = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="older",
        )
        newer = MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.second,
            receiver=self.first,
            text="newer",
        )
        base = timezone.now() - timedelta(hours=1)
        MessageModel.objects.filter(pk=older.pk).update(timestamp=base)
        MessageModel.objects.filter(pk=newer.pk).update(timestamp=base + timedelta(seconds=1))

        call_command("backfill_chat_v2", batch_size=1, max_batches=1, stdout=StringIO())
        self.assertEqual(
            MessageModel.objects.filter(chat_room=self.room, sequence__isnull=False).count(),
            1,
        )
        call_command("backfill_chat_v2", batch_size=1, max_batches=10, stdout=StringIO())
        ordered = list(
            MessageModel.objects.filter(chat_room=self.room)
            .order_by("sequence")
            .values_list("text", "sequence")
        )
        self.assertEqual(ordered, [("older", 1), ("newer", 2)])

        unsafe_old = MessageModel.objects.create(
            chat_room=self.other_room,
            couple=self.other_room.couple,
            sender=self.outsider,
            receiver=self.fourth,
            text="unsafe-old",
        )
        unsafe_new = MessageModel.objects.create(
            chat_room=self.other_room,
            couple=self.other_room.couple,
            sender=self.fourth,
            receiver=self.outsider,
            text="unsafe-new",
            sequence=1,
        )
        MessageModel.objects.filter(pk=unsafe_old.pk).update(timestamp=base)
        MessageModel.objects.filter(pk=unsafe_new.pk).update(timestamp=base + timedelta(seconds=1))
        with self.assertRaises(CommandError):
            call_command("backfill_chat_v2", check=True, stdout=StringIO())

    def test_v2_writes_wait_for_legacy_sequence_backfill(self):
        MessageModel.objects.create(
            chat_room=self.room,
            couple=self.room.couple,
            sender=self.first,
            receiver=self.second,
            text="legacy backlog",
        )
        with self.enabled():
            response = self.send()
        self.assertEqual(response.status_code, 503)

    def test_outbox_failure_uses_bounded_backoff_and_stable_retry(self):
        with self.enabled():
            self.send()
        now = timezone.now()

        class BrokenLayer:
            async def group_send(self, group, event):
                raise OSError("synthetic")

        result = dispatch_pending(
            channel_layer=BrokenLayer(),
            now_fn=lambda: now,
            jitter_fn=lambda low, high: 0,
        )
        self.assertEqual(result, {"claimed": 1, "published": 0, "failed": 1})
        row = ChatOutbox.objects.get()
        self.assertEqual(row.state, ChatOutbox.State.PENDING)
        self.assertEqual(row.last_error_code, "channel_unavailable")
        self.assertGreater(row.available_at, now)


class ChatV2ManagementCommandTests(TestCase):
    @override_settings(
        CHAT_V2_ENCRYPTED_WRITES_ENABLED=False,
        CHAT_V2_PROTOCOL_STATUS="adapter_required",
        CHAT_V2_TEST_ADAPTER_ENABLED=False,
        CHAT_V2_ALLOWED_USER_IDS={101, 202},
        CHAT_V2_REQUIRE_DEVICE_SESSION=True,
        CHAT_V2_ATTACHMENT_STAGING_ENABLED=False,
    )
    def test_status_reports_safe_configuration_and_backlog_counts(self):
        output = StringIO()

        call_command("chat_v2_status", stdout=output)

        values = dict(
            line.split("=", 1)
            for line in output.getvalue().splitlines()
            if "=" in line
        )
        self.assertEqual(values["chat_v2_schema_ready"], "true")
        self.assertEqual(values["chat_v2_required_migrations_applied"], "true")
        self.assertEqual(values["protocol_status"], "adapter_required")
        self.assertEqual(values["encrypted_writes_enabled"], "false")
        self.assertEqual(values["test_adapter_enabled"], "false")
        self.assertEqual(values["allowlisted_user_count"], "2")
        self.assertEqual(values["requires_device_session"], "true")
        self.assertEqual(values["attachment_staging_enabled"], "false")
        self.assertEqual(values["legacy_unsequenced_messages"], "0")
        self.assertEqual(values["outbox_pending"], "0")
        self.assertEqual(values["outbox_retrying"], "0")
        self.assertNotIn("101,202", output.getvalue())

    @patch("chat.management.commands.dispatch_chat_outbox.signal.signal")
    @patch("chat.management.commands.dispatch_chat_outbox.Event")
    @patch("chat.management.commands.dispatch_chat_outbox.dispatch_pending")
    def test_outbox_watch_dispatches_a_bounded_batch_and_stops_cleanly(
        self,
        dispatch,
        event_type,
        signal_handler,
    ):
        dispatch.return_value = {"claimed": 0, "published": 0, "failed": 0}
        stopped = event_type.return_value
        stopped.is_set.return_value = False
        stopped.wait.return_value = True
        output = StringIO()

        call_command(
            "dispatch_chat_outbox",
            watch=True,
            poll_interval=0.01,
            limit=7,
            stdout=output,
        )

        dispatch.assert_called_once_with(batch_size=7)
        stopped.wait.assert_called_once_with(0.01)
        self.assertEqual(signal_handler.call_count, 4)
        self.assertIn("chat_outbox_worker=started", output.getvalue())
        self.assertIn("chat_outbox_worker=stopped", output.getvalue())

    @patch("chat.management.commands.run_chat_media_cleanup.signal.signal")
    @patch("chat.management.commands.run_chat_media_cleanup.Event")
    @patch("chat.management.commands.run_chat_media_cleanup.call_command")
    def test_media_cleanup_watch_runs_the_existing_bounded_command(
        self,
        cleanup,
        event_type,
        signal_handler,
    ):
        stopped = event_type.return_value
        stopped.is_set.return_value = False
        stopped.wait.return_value = True
        output = StringIO()

        call_command(
            "run_chat_media_cleanup",
            poll_interval=0.01,
            limit=9,
            stdout=output,
        )

        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args, ("cleanup_chat_media",))
        self.assertEqual(cleanup.call_args.kwargs["limit"], 9)
        stopped.wait.assert_called_once_with(0.01)
        self.assertEqual(signal_handler.call_count, 4)
        self.assertIn("chat_media_cleanup_worker=started", output.getvalue())
        self.assertIn("chat_media_cleanup_worker=stopped", output.getvalue())

    def test_watch_commands_reject_non_positive_intervals(self):
        for command, options in (
            ("dispatch_chat_outbox", {"watch": True}),
            ("run_chat_media_cleanup", {}),
        ):
            with self.subTest(command=command), self.assertRaises(CommandError):
                call_command(command, poll_interval=0, **options)


class RelationshipAcceptancePostgresTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_acceptance_replays_one_relationship_room(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(100), make_user(101)
        request = CoupleConnectionModel.objects.create(
            sender_number=first.phone_number,
            receiver_number=second.phone_number,
            connection_status="PENDING",
        )

        def accept_once():
            close_old_connections()
            try:
                actor = UserModel.objects.get(pk=second.pk)
                return accept_connection(connection_id=request.pk, actor=actor).chat_room.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            room_ids = list(executor.map(lambda _: accept_once(), range(2)))
        self.assertEqual(len(set(room_ids)), 1)
        self.assertEqual(ChatRoom.objects.filter(couple__couple_connection=request).count(), 1)

    def test_concurrent_opposite_invites_create_one_pending_identity(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(102), make_user(103)

        def request_once(sender_id, receiver_id):
            close_old_connections()
            try:
                sender = UserModel.objects.get(pk=sender_id)
                receiver = UserModel.objects.get(pk=receiver_id)
                return create_connection_request(
                    sender_number=sender.phone_number,
                    receiver_number=receiver.phone_number,
                    actor=sender,
                    operation_id=uuid4(),
                ).connection.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(request_once, first.pk, second.pk),
                executor.submit(request_once, second.pk, first.pk),
            ]
            connection_ids = []
            conflicts = 0
            for future in futures:
                try:
                    connection_ids.append(future.result())
                except RelationshipConflict:
                    conflicts += 1
        self.assertEqual(len(connection_ids), 1)
        self.assertEqual(conflicts, 1)
        self.assertEqual(
            CoupleConnectionModel.objects.filter(connection_status="PENDING").count(),
            1,
        )

    def test_v2_send_and_relationship_end_are_linearized(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(104), make_user(105)
        relationship = make_relationship(first, second)
        writer_holds_locks = Event()
        release_writer = Event()
        end_started = Event()
        completion_order = []

        from chat.services import v2 as v2_service
        original_room_pilot = v2_service._require_room_pilot

        def pause_after_room_lock(room):
            writer_holds_locks.set()
            if not release_writer.wait(timeout=10):
                raise RuntimeError("test writer was not released")
            return original_room_pilot(room)

        def write_once():
            close_old_connections()
            try:
                actor = UserModel.objects.get(pk=first.pk)
                result = send_encrypted_message(
                    room_id=relationship.chat_room.pk,
                    user=actor,
                    operation_id=uuid4(),
                    content_kind="text",
                    envelope={"ciphertext": "race"},
                )
                completion_order.append("write")
                return result.message.pk
            finally:
                close_old_connections()

        def end_once():
            close_old_connections()
            try:
                end_started.set()
                actor = UserModel.objects.get(pk=first.pk)
                end_connection(connection_id=relationship.connection.pk, actor=actor)
                completion_order.append("end")
            finally:
                close_old_connections()

        enabled = override_settings(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="test_adapter",
            CHAT_V2_TEST_ADAPTER_ENABLED=True,
            CHAT_V2_ALLOWED_USER_IDS={first.pk, second.pk},
            CHAT_V2_REQUIRE_DEVICE_SESSION=False,
        )
        with enabled, patch(
            "chat.services.v2._require_room_pilot",
            side_effect=pause_after_room_lock,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(write_once)
            self.assertTrue(writer_holds_locks.wait(timeout=5))
            ending = executor.submit(end_once)
            self.assertTrue(end_started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                ending.result(timeout=0.2)
            release_writer.set()
            writer.result(timeout=10)
            ending.result(timeout=10)

        self.assertEqual(completion_order, ["write", "end"])
        with enabled, self.assertRaises(ChatV2NotFound):
            send_encrypted_message(
                room_id=relationship.chat_room.pk,
                user=first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "after-revocation"},
            )

    def test_legacy_send_and_relationship_end_are_linearized(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(106), make_user(107)
        relationship = make_relationship(first, second)
        writer_holds_locks = Event()
        release_writer = Event()
        end_started = Event()
        completion_order = []

        def pause_after_room_lock(media_ids, *, user, chat_room):
            writer_holds_locks.set()
            if not release_writer.wait(timeout=10):
                raise RuntimeError("test writer was not released")
            return []

        def write_once():
            close_old_connections()
            try:
                actor = UserModel.objects.get(pk=first.pk)
                message, _ = send_message(
                    user=actor,
                    payload=SendMessageInput(
                        chat_room_id=str(relationship.chat_room.pk),
                        text="legacy race",
                    ),
                )
                completion_order.append("write")
                return message.pk
            finally:
                close_old_connections()

        def end_once():
            close_old_connections()
            try:
                end_started.set()
                actor = UserModel.objects.get(pk=first.pk)
                end_connection(connection_id=relationship.connection.pk, actor=actor)
                completion_order.append("end")
            finally:
                close_old_connections()

        with patch(
            "chat.services.message_service._get_media_objects",
            side_effect=pause_after_room_lock,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(write_once)
            self.assertTrue(writer_holds_locks.wait(timeout=5))
            ending = executor.submit(end_once)
            self.assertTrue(end_started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                ending.result(timeout=0.2)
            release_writer.set()
            writer.result(timeout=10)
            ending.result(timeout=10)

        self.assertEqual(completion_order, ["write", "end"])
        with self.assertRaises(ChatRoomNotFoundError):
            send_message(
                user=first,
                payload=SendMessageInput(
                    chat_room_id=str(relationship.chat_room.pk),
                    text="after revocation",
                ),
            )

    def test_v2_send_and_device_revocation_are_linearized(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(108), make_user(109)
        relationship = make_relationship(first, second)
        session = DeviceSession.objects.create(
            user=first,
            refresh_digest="synthetic",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        writer_holds_session = Event()
        release_writer = Event()
        revoke_started = Event()
        completion_order = []

        from chat.services import v2 as v2_service
        original_other_user = v2_service._other_user

        def pause_after_session_lock(room, user):
            writer_holds_session.set()
            if not release_writer.wait(timeout=10):
                raise RuntimeError("test writer was not released")
            return original_other_user(room, user)

        def write_once():
            close_old_connections()
            try:
                actor = UserModel.objects.get(pk=first.pk)
                send_encrypted_message(
                    room_id=relationship.chat_room.pk,
                    user=actor,
                    operation_id=uuid4(),
                    content_kind="text",
                    envelope={"ciphertext": "session-race"},
                    device_session_id=session.pk,
                )
                completion_order.append("write")
            finally:
                close_old_connections()

        def revoke_once():
            close_old_connections()
            try:
                revoke_started.set()
                DeviceSession.objects.filter(pk=session.pk).update(
                    revoked_at=timezone.now()
                )
                completion_order.append("revoke")
            finally:
                close_old_connections()

        enabled = override_settings(
            CHAT_V2_ENCRYPTED_WRITES_ENABLED=True,
            CHAT_V2_PROTOCOL_STATUS="test_adapter",
            CHAT_V2_TEST_ADAPTER_ENABLED=True,
            CHAT_V2_ALLOWED_USER_IDS={first.pk, second.pk},
            CHAT_V2_REQUIRE_DEVICE_SESSION=True,
        )
        with enabled, patch(
            "chat.services.v2._other_user",
            side_effect=pause_after_session_lock,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(write_once)
            self.assertTrue(writer_holds_session.wait(timeout=5))
            revoker = executor.submit(revoke_once)
            self.assertTrue(revoke_started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                revoker.result(timeout=0.2)
            release_writer.set()
            writer.result(timeout=10)
            revoker.result(timeout=10)

        self.assertEqual(completion_order, ["write", "revoke"])
        with enabled, self.assertRaises(ChatV2Validation):
            send_encrypted_message(
                room_id=relationship.chat_room.pk,
                user=first,
                operation_id=uuid4(),
                content_kind="text",
                envelope={"ciphertext": "after-revoke"},
                device_session_id=session.pk,
            )

    def test_media_cleanup_and_legacy_claim_are_linearized(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock behavior")
        first, second = make_user(110), make_user(111)
        relationship = make_relationship(first, second)
        media = MediaModel.objects.create(
            file=SimpleUploadedFile(
                "cleanup-race.jpg",
                synthetic_jpeg(),
                content_type="image/jpeg",
            ),
            owner=first,
            chat_room=relationship.chat_room,
        )
        MediaModel.objects.filter(pk=media.pk).update(
            uploaded_at=timezone.now() - timedelta(days=2)
        )
        storage = media.file.storage
        storage_name = media.file.name
        cleanup_holds_row = Event()
        release_cleanup = Event()
        send_started = Event()
        completion_order = []
        original_delete = storage.delete

        def pause_storage_delete(name):
            cleanup_holds_row.set()
            if not release_cleanup.wait(timeout=10):
                raise RuntimeError("test cleanup was not released")
            return original_delete(name)

        def cleanup_once():
            close_old_connections()
            try:
                call_command("cleanup_chat_media", limit=10, stdout=StringIO())
                completion_order.append("cleanup")
            finally:
                close_old_connections()

        def claim_once():
            close_old_connections()
            try:
                send_started.set()
                actor = UserModel.objects.get(pk=first.pk)
                with self.assertRaises(InvalidMessagePayloadError):
                    send_message(
                        user=actor,
                        payload=SendMessageInput(
                            chat_room_id=str(relationship.chat_room.pk),
                            text="must not claim a deleted upload",
                            media_ids=[media.pk],
                        ),
                    )
                completion_order.append("claim_denied")
            finally:
                close_old_connections()

        with patch.object(storage, "delete", side_effect=pause_storage_delete), \
             ThreadPoolExecutor(max_workers=2) as executor:
            cleanup = executor.submit(cleanup_once)
            self.assertTrue(cleanup_holds_row.wait(timeout=5))
            claim = executor.submit(claim_once)
            self.assertTrue(send_started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                claim.result(timeout=0.2)
            release_cleanup.set()
            cleanup.result(timeout=10)
            claim.result(timeout=10)

        self.assertCountEqual(completion_order, ["cleanup", "claim_denied"])
        self.assertFalse(MediaModel.objects.filter(pk=media.pk).exists())
        self.assertFalse(storage.exists(storage_name))
        self.assertFalse(MessageModel.objects.filter(chat_room=relationship.chat_room).exists())
