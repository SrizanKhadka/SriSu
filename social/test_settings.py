from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from authentication.models import UserModel
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from social.services.relationship_service import accept_connection


class SettingsTests(TestCase):
    def setUp(self):
        self.one = UserModel.objects.create_user(phone_number='+15005550001', full_name='Alex')
        self.two = UserModel.objects.create_user(phone_number='+15005550002', full_name='Sam')
        self.other = UserModel.objects.create_user(phone_number='+15005550003')
        self.connection = CoupleConnectionModel.objects.create(sender_number=self.one.phone_number,
            receiver_number=self.two.phone_number, connection_status='PENDING')
        self.client = APIClient()
        self.client.force_authenticate(self.one)

    def connect(self):
        self.result = accept_connection(connection_id=self.connection.pk, actor=self.two)
        self.connection.refresh_from_db()
        return self.result

    def disconnect(self, revision=None):
        return self.client.post('/api/social/settings/disconnect/', {
            'connection_id': self.connection.pk,
            'expected_revision': revision or self.connection.revision,
        }, format='json')

    def test_empty_pending_and_requires_auth(self):
        response = self.client.get('/api/social/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['data']['partner'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.disconnect().status_code, 409)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/social/settings/').status_code, 401)
        self.assertEqual(self.disconnect().status_code, 401)

    def test_connected_projection_and_unrelated_account(self):
        self.connect()
        self.connection.accepted_at = timezone.now()-timedelta(days=248)
        self.connection.save(update_fields=['accepted_at'])
        data = self.client.get('/api/social/settings/').data['data']['partner']
        self.assertEqual(data['name'], 'Sam')
        self.assertEqual(data['user_id'], self.two.pk)
        self.assertEqual(data['connected_days'], 248)
        self.assertNotIn('phone_number', data)
        self.assertNotIn('bio', data)
        self.client.force_authenticate(self.other)
        self.assertIsNone(self.client.get('/api/social/settings/').data['data']['partner'])
        self.assertEqual(self.disconnect().status_code, 404)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.connection_status, 'ACCEPTED')

    def test_projection_uses_two_bounded_queries(self):
        self.connect()
        with self.assertNumQueries(2):
            response = self.client.get('/api/social/settings/')
        self.assertEqual(response.status_code, 200)

    def test_revision_guard_and_atomic_disconnect_replay(self):
        result = self.connect()
        self.assertEqual(self.disconnect(self.connection.revision+1).status_code, 409)
        self.assertEqual(CoupleMembershipModel.objects.filter(couple=result.couple, ended_at__isnull=True).count(),2)
        with patch('social.services.relationship_service.schedule_matrix_room_revocation') as schedule:
            self.assertEqual(self.disconnect().status_code, 200)
            self.assertEqual(self.disconnect().status_code, 200)
            self.assertEqual(schedule.call_count,1)
        self.assertFalse(CoupleMembershipModel.objects.filter(couple=result.couple, ended_at__isnull=True).exists())
        self.one.refresh_from_db(); self.two.refresh_from_db()
        self.assertFalse(self.one.is_engaged); self.assertFalse(self.two.is_engaged)
        self.assertIsNone(self.client.get('/api/social/settings/').data['data']['partner'])
        self.assertTrue(CoupleModel.objects.filter(pk=result.couple.pk).exists())

    def test_stale_confirmation_cannot_disconnect_new_relationship(self):
        old = self.connect()
        self.assertEqual(self.disconnect().status_code,200)
        next_connection = CoupleConnectionModel.objects.create(sender_number=self.one.phone_number,
            receiver_number=self.other.phone_number, connection_status='PENDING')
        newer = accept_connection(connection_id=next_connection.pk,actor=self.other)
        replayed = self.disconnect()
        self.assertEqual(replayed.status_code,200)
        self.assertEqual(replayed.data['data']['partner']['connection_id'],next_connection.pk)
        next_connection.refresh_from_db()
        self.assertEqual(next_connection.connection_status,'ACCEPTED')
        self.assertEqual(CoupleMembershipModel.objects.filter(couple=newer.couple,ended_at__isnull=True).count(),2)

    def test_strict_fields_and_invalid_id(self):
        self.connect()
        for data in ({}, {'connection_id':-1,'expected_revision':1},
                     {'connection_id':self.connection.pk,'expected_revision':1,'user':self.other.pk}):
            self.assertEqual(self.client.post('/api/social/settings/disconnect/',data,format='json').status_code,400)

    def test_mismatched_partner_membership_is_not_exposed(self):
        result = self.connect()
        CoupleMembershipModel.objects.filter(couple=result.couple, user=self.two).update(user=self.other)
        response = self.client.get('/api/social/settings/')
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('partner', response.data.get('data', {}))

    def test_nonparticipant_membership_cannot_read_connection_details(self):
        result = self.connect()
        CoupleMembershipModel.objects.filter(couple=result.couple, user=self.one).update(user=self.other)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/social/settings/').status_code, 409)

    def test_disabled_or_missing_partner_is_not_reported_as_connected(self):
        result = self.connect()
        self.two.is_active = False
        self.two.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/social/settings/').status_code, 409)
        CoupleMembershipModel.objects.filter(couple=result.couple, user=self.two).update(ended_at=timezone.now())
        self.assertEqual(self.client.get('/api/social/settings/').status_code, 409)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, connections
from django.test import TransactionTestCase
from social.services.relationship_service import end_connection


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class ConcurrentSettingsTests(TransactionTestCase):
    setUp = SettingsTests.setUp

    def test_both_partners_confirm_once_without_double_revocation(self):
        result = accept_connection(connection_id=self.connection.pk, actor=self.two)
        self.connection.refresh_from_db()
        revision = self.connection.revision
        barrier = Barrier(2)
        def end(user_id):
            try:
                actor = UserModel.objects.get(pk=user_id)
                barrier.wait(timeout=10)
                return end_connection(connection_id=self.connection.pk, actor=actor,
                                      expected_revision=revision).revision
            finally:
                connections.close_all()
        with patch('social.services.relationship_service.schedule_matrix_room_revocation') as schedule:
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(end, [self.one.pk,self.two.pk]))
            self.assertEqual(values,[revision+1,revision+1])
            self.assertEqual(schedule.call_count,1)
        self.assertFalse(CoupleMembershipModel.objects.filter(couple=result.couple,ended_at__isnull=True).exists())
