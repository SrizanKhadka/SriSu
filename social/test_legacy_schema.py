"""PostgreSQL compatibility for columns retained on an older development host."""
from importlib import import_module
from unittest import skipUnless
from django.apps import apps
from django.db import connection, IntegrityError, transaction
from rest_framework.test import APITestCase
from authentication.models import UserModel
from chat.models import ChatRoom
from social.models import CoupleConnectionModel


@skipUnless(connection.vendor == 'postgresql', 'Retained PostgreSQL schema regression')
class PartnerInsertCompatibilityTests(APITestCase):
    def setUp(self):
        self.first,self.second,self.third,self.fourth=[UserModel.objects.create_user(
            phone_number=f'+1500555020{i}',full_name=f'Synthetic {i}',is_phone_verified=True,is_profile_complete=True) for i in range(1,5)]
        self.payload=dict(sender_number=self.first.phone_number,receiver_number=self.second.phone_number)

    def retained(self, drop_default=True):
        # revision is now an ORM-owned lifecycle field. Only last_sequence remains unmapped.
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE chat_chatroom ADD COLUMN last_sequence bigint NOT NULL DEFAULT 23 CHECK (last_sequence >= 0)')
        self.old_room=ChatRoom.objects.create(user_one=self.third,user_two=self.fourth)
        self.history=CoupleConnectionModel.objects.create(**self.payload,connection_status='REJECTED',revision=9)
        if drop_default:
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('ALTER TABLE chat_chatroom ALTER COLUMN last_sequence DROP DEFAULT')

    def apply_compatibility(self):
        migration=import_module('social.migrations.0014_partner_insert_compatibility')
        with connection.schema_editor() as editor:migration.supply_retained_defaults(apps,editor)

    def test_missing_retained_default_repaired_without_changing_history(self):
        self.retained()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():ChatRoom.objects.create(user_one=self.first,user_two=self.second)
        self.apply_compatibility();self.apply_compatibility()
        self.client.force_authenticate(self.first)
        response=self.client.post('/api/social/connect-couple/',self.payload,format='json')
        self.assertEqual(response.status_code,201)
        retry=self.client.post('/api/social/connect-couple/',self.payload,format='json')
        self.assertEqual(retry.status_code,200)
        self.client.force_authenticate(self.second)
        result=self.client.put(f"/api/social/connect-couple/{response.data['data']['id']}/",self.payload|{'connection_status':'ACCEPTED'},format='json')
        self.assertEqual(result.status_code,200)
        self.assertTrue(ChatRoom.objects.filter(user_one=self.first,user_two=self.second,couple__isnull=False).exists())
        self.history.refresh_from_db();self.assertEqual(self.history.revision,9)
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_sequence FROM chat_chatroom WHERE id=%s',[self.old_room.pk]);self.assertEqual(cursor.fetchone()[0],23)

    def test_existing_defaults_are_preserved(self):
        self.retained(drop_default=False);self.apply_compatibility()
        room=ChatRoom.objects.create(user_one=self.first,user_two=self.second)
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_sequence FROM chat_chatroom WHERE id=%s',[room.pk]);self.assertEqual(cursor.fetchone()[0],23)

    def test_current_schema_without_retained_columns_is_supported(self):
        self.apply_compatibility();self.apply_compatibility()
        self.assertIsNotNone(ChatRoom.objects.create(user_one=self.first,user_two=self.second).pk)
