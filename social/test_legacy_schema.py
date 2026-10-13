"""PostgreSQL reproduction of retained columns from the earlier chat branch."""
from importlib import import_module
from unittest import skipUnless

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from rest_framework.test import APITestCase

from authentication.models import UserModel
from chat.models import ChatRoom
from couple_chat.models import Room
from social.models import CoupleConnectionModel
from social.services.relationship_service import invite


@skipUnless(connection.vendor == "postgresql", "Retained PostgreSQL schema regression")
class PartnerInsertCompatibilityTests(APITestCase):
    def setUp(self):
        self.first, self.second, self.third, self.fourth = [
            UserModel.objects.create_user(phone_number=f"+977980020000{i}",
                full_name=f"Synthetic {i}", is_phone_verified=True, is_profile_complete=True)
            for i in range(1, 5)
        ]
        self.payload = {"sender_number": self.first.phone_number,
                        "receiver_number": self.second.phone_number}

    def add_retained_columns(self, *, drop_defaults=True):
        # TestCase rolls back this transactional DDL and all synthetic records.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE social_coupleconnectionmodel ADD COLUMN revision bigint NOT NULL DEFAULT 9 CHECK (revision >= 0)")
            cursor.execute("ALTER TABLE chat_chatroom ADD COLUMN last_sequence bigint NOT NULL DEFAULT 23 CHECK (last_sequence >= 0)")
        self.history = CoupleConnectionModel.objects.create(**self.payload, connection_status="REJECTED")
        self.old_room = ChatRoom.objects.create(user_one=self.third, user_two=self.fourth)
        if drop_defaults:
            with connection.cursor() as cursor:
                # The fixture rows and DDL share TestCase's transaction. Finish
                # deferred FK checks before changing that table's defaults.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute("ALTER TABLE social_coupleconnectionmodel ALTER COLUMN revision DROP DEFAULT")
                cursor.execute("ALTER TABLE chat_chatroom ALTER COLUMN last_sequence DROP DEFAULT")

    def migrate_defaults(self):
        migration = import_module("social.migrations.0014_partner_insert_compatibility")
        with connection.schema_editor() as editor:
            migration.supply_retained_defaults(apps, editor)

    def create_and_accept(self):
        self.client.force_authenticate(self.first)
        response = self.client.post("/api/social/connect-couple/", self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        invitation_id = response.data["data"]["id"]
        retry = self.client.post("/api/social/connect-couple/", self.payload, format="json")
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data["data"]["id"], invitation_id)
        self.client.force_authenticate(self.second)
        accepted = self.client.put(f"/api/social/connect-couple/{invitation_id}/",
            {**self.payload, "connection_status": "ACCEPTED"}, format="json")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertTrue(Room.objects.filter(pk=accepted.data["couple_chat"]["id"]).exists())
        return invitation_id

    def test_retained_required_columns_reproduce_failure_then_invite_retry_and_accept_work(self):
        self.add_retained_columns()
        with self.assertRaises(IntegrityError) as invitation_error:
            with transaction.atomic():
                invite(self.first, self.second.phone_number)
        self.assertEqual(invitation_error.exception.__cause__.diag.column_name, "revision")
        with self.assertRaises(IntegrityError) as room_error:
            with transaction.atomic():
                ChatRoom.objects.create(user_one=self.first, user_two=self.second)
        self.assertEqual(room_error.exception.__cause__.diag.column_name, "last_sequence")
        self.migrate_defaults()
        self.migrate_defaults()  # Reapplication must preserve counters and defaults.
        invitation_id = self.create_and_accept()
        self.history.refresh_from_db()
        self.assertEqual(self.history.connection_status, "REJECTED")
        with connection.cursor() as cursor:
            cursor.execute("SELECT revision FROM social_coupleconnectionmodel WHERE id = %s", [self.history.pk])
            self.assertEqual(cursor.fetchone(), (9,))
            cursor.execute("SELECT revision FROM social_coupleconnectionmodel WHERE id = %s", [invitation_id])
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.execute("SELECT last_sequence FROM chat_chatroom WHERE id = %s", [self.old_room.pk])
            self.assertEqual(cursor.fetchone(), (23,))
            cursor.execute("SELECT last_sequence FROM chat_chatroom WHERE user_one_id = %s AND user_two_id = %s",
                           [self.first.pk, self.second.pk])
            self.assertEqual(cursor.fetchone(), (0,))

    def test_existing_explicit_defaults_are_preserved(self):
        self.add_retained_columns(drop_defaults=False)
        self.migrate_defaults()
        invitation_id = self.create_and_accept()
        with connection.cursor() as cursor:
            cursor.execute("SELECT revision FROM social_coupleconnectionmodel WHERE id = %s", [invitation_id])
            self.assertEqual(cursor.fetchone(), (9,))
            cursor.execute("SELECT last_sequence FROM chat_chatroom WHERE user_one_id = %s AND user_two_id = %s",
                           [self.first.pk, self.second.pk])
            self.assertEqual(cursor.fetchone(), (23,))

    def test_fresh_schema_remains_unchanged_and_partner_flow_works(self):
        with connection.cursor() as cursor:
            before = connection.introspection.get_table_description(cursor, CoupleConnectionModel._meta.db_table)
        self.migrate_defaults()
        with connection.cursor() as cursor:
            after = connection.introspection.get_table_description(cursor, CoupleConnectionModel._meta.db_table)
        self.assertEqual(before, after)
        self.create_and_accept()
