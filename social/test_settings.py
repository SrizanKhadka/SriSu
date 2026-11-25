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

    def nickname(self, value, *, revision=None, connection=None, nickname_revision=None):
        current = self.client.get('/api/social/settings/').data.get('data', {}).get('partner') or {}
        return self.client.put('/api/social/settings/partner-nickname/', {
            'connection_id': connection or self.connection.pk,
            'expected_revision': revision or self.connection.revision,
            'expected_nickname_revision': nickname_revision or current.get('nickname_revision', 1),
            'nickname': value,
        }, format='json')

    def test_private_nickname_round_trip_and_viewer_isolation(self):
        self.connect()
        first = self.nickname('  Sunshine  🌻  ')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIn('no-store', first['Cache-Control'])
        partner = first.data['data']['partner']
        self.assertEqual(partner['name'], 'Sam')
        self.assertEqual(partner['nickname'], 'Sunshine 🌻')
        self.assertEqual(partner['nickname_revision'], 2)
        self.assertEqual(partner['revision'], self.connection.revision)
        with self.assertNumQueries(2):
            self.assertEqual(self.client.get('/api/social/settings/').data['data']['partner']['nickname'], 'Sunshine 🌻')
        self.client.force_authenticate(self.two)
        other = self.client.get('/api/social/settings/').data['data']['partner']
        self.assertIsNone(other['nickname'])
        self.assertEqual(other['name'], 'Alex')
        self.assertEqual(other['nickname_revision'], 1)
        response = self.nickname('Moon')
        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(self.one)
        self.assertEqual(self.client.get('/api/social/settings/').data['data']['partner']['nickname'], 'Sunshine 🌻')
        self.two.refresh_from_db(); self.assertEqual(self.two.full_name, 'Sam')
        # The legacy shared member label remains independent of private nicknames.
        member = CoupleMembershipModel.objects.get(user=self.one)
        self.assertIsNone(member.nickname)

    def test_nickname_unicode_validation_and_clear(self):
        self.connect()
        for invalid in ('x'*41, 'bad\nname', 'bad\x00name', '\u202eFake', '\u200b', '\u200d', '\ufe0f', 13, {}, []):
            response = self.nickname(invalid)
            self.assertEqual(response.status_code, 400, (invalid, response.data))
        self.assertEqual(self.nickname('🌻'*40).status_code, 200)
        self.assertEqual(self.nickname('Cafe\u0301 👩\u200d❤️\u200d👩').data['data']['partner']['nickname'], 'Café 👩\u200d❤️\u200d👩')
        cleared = self.nickname('   ').data['data']['partner']
        self.assertIsNone(cleared['nickname'])
        self.assertEqual(self.nickname(None).data['data']['partner']['nickname_revision'], cleared['nickname_revision'])

    def test_nickname_replay_and_revision_conflict(self):
        self.connect()
        self.assertEqual(self.nickname('Sun', nickname_revision=1).status_code, 200)
        repeated = self.nickname('Sun', nickname_revision=1)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['data']['partner']['nickname_revision'], 2)
        self.assertEqual(self.nickname('Moon', nickname_revision=1).status_code, 409)
        self.assertEqual(self.nickname('Moon', revision=self.connection.revision+1).status_code, 409)
        self.assertEqual(self.client.get('/api/social/settings/').data['data']['partner']['nickname'], 'Sun')

    def test_nickname_requires_current_authorized_pair(self):
        self.assertEqual(self.nickname('Sun').status_code, 409)  # Pending invitation.
        self.connect()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.nickname('Sun').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.nickname('Sun').status_code, 401)
        self.client.force_authenticate(self.one)
        member = CoupleMembershipModel.objects.get(user=self.two)
        member.user = self.other; member.save(update_fields=['user'])
        self.assertEqual(self.nickname('Sun').status_code, 409)

    def test_nickname_strict_input_and_legacy_shared_serialization(self):
        self.connect()
        data = dict(connection_id=self.connection.pk, expected_revision=self.connection.revision,
            expected_nickname_revision=1, nickname='Sun')
        for invalid in ({}, data | {'owner': self.two.pk}, data | {'expected_nickname_revision': 0},
                        data | {'connection_id': -1}):
            self.assertEqual(self.client.put('/api/social/settings/partner-nickname/',invalid,format='json').status_code,400)
        self.nickname('Sun')
        from social.api.serializers import CoupleMembershipSerializer
        value = CoupleMembershipSerializer(CoupleMembershipModel.objects.get(user=self.one)).data
        self.assertNotIn('partner_nickname', value)
        self.assertNotIn('partner_nickname_revision', value)

    def test_private_nickname_fields_are_excluded_from_generic_admin_forms(self):
        from django.contrib import admin
        from django.test import RequestFactory
        self.connect(); self.nickname('Hidden personal label')
        request=RequestFactory().get('/admin/social/couplemembershipmodel/')
        request.user=self.one
        registered=admin.site._registry[CoupleMembershipModel]
        member=CoupleMembershipModel.objects.get(user=self.one)
        for instance in (None, member):
            fields=registered.get_form(request, obj=instance).base_fields
            self.assertNotIn('partner_nickname',fields)
            self.assertNotIn('partner_nickname_revision',fields)
        from social.api.serializers import CoupleModelSerializer
        data=CoupleModelSerializer(self.result.couple,context={'request':request}).data
        self.assertNotIn('Hidden personal label',str(data))
        self.assertNotIn('partner_nickname',str(data))

    def test_unlink_erases_alias_and_relink_does_not_reuse_it(self):
        self.connect(); self.nickname('Sun')
        old_revision = self.connection.revision
        self.assertEqual(self.disconnect().status_code, 200)
        old = CoupleMembershipModel.all_objects.get(couple=self.result.couple, user=self.one)
        self.assertEqual(old.partner_nickname, '')
        self.assertEqual(self.nickname('Moon', revision=old_revision).status_code, 409)
        newer = CoupleConnectionModel.objects.create(sender_number=self.one.phone_number,
            receiver_number=self.two.phone_number, connection_status='PENDING')
        accept_connection(connection_id=newer.pk, actor=self.two)
        context = self.client.get('/api/social/settings/').data['data']['partner']
        self.assertIsNone(context['nickname']); self.assertEqual(context['nickname_revision'],1)
        self.assertEqual(self.nickname('Moon', revision=old_revision).status_code, 409)

    def test_alias_lookup_rechecks_membership_and_never_uses_shared_nickname(self):
        self.connect()
        from social.services.partner_nicknames import aliases_for
        member = CoupleMembershipModel.objects.get(user=self.one)
        member.nickname = 'Shared legacy label'; member.save(update_fields=['nickname'])
        self.assertEqual(aliases_for(self.one), {})
        self.nickname('Sun')
        self.assertEqual(aliases_for(self.one), {self.two.pk:'Sun'})
        self.assertEqual(aliases_for(self.two), {})
        self.nickname('Moon')
        self.assertEqual(aliases_for(self.one), {self.two.pk:'Moon'})
        self.disconnect()
        self.assertEqual(aliases_for(self.one), {})


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


    def test_concurrent_private_nickname_edits_reject_lost_updates(self):
        from social.services.partner_nicknames import set_partner_nickname, NicknameConflict
        accept_connection(connection_id=self.connection.pk, actor=self.two)
        self.connection.refresh_from_db()
        barrier = Barrier(2)
        def change(label):
            try:
                user = UserModel.objects.get(pk=self.one.pk)
                barrier.wait(timeout=10)
                try:
                    value = set_partner_nickname(user, connection_id=self.connection.pk,
                        expected_revision=self.connection.revision, expected_nickname_revision=1, nickname=label, notify_partner=True)
                    return value.partner_nickname_revision
                except NicknameConflict:
                    return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(change, ['Sun', 'Moon']))
        self.assertCountEqual(result, [2, 'conflict'])
        from notifications.models import Event
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)
        self.assertIn(CoupleMembershipModel.objects.get(user=self.one).partner_nickname, ['Sun','Moon'])

    def test_concurrent_duplicate_nickname_writes_share_one_revision(self):
        from social.services.partner_nicknames import set_partner_nickname
        accept_connection(connection_id=self.connection.pk, actor=self.two)
        self.connection.refresh_from_db()
        barrier = Barrier(2)
        def change(_):
            try:
                user = UserModel.objects.get(pk=self.one.pk)
                barrier.wait(timeout=10)
                return set_partner_nickname(user, connection_id=self.connection.pk,
                    expected_revision=self.connection.revision, expected_nickname_revision=1, nickname='Sun', notify_partner=True).partner_nickname_revision
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(change, range(2))), [2,2])
        from notifications.models import Event
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)

    def test_nickname_and_unlink_cannot_leave_private_alias_on_ended_membership(self):
        from social.services.partner_nicknames import set_partner_nickname, NicknameConflict
        accepted = accept_connection(connection_id=self.connection.pk, actor=self.two)
        self.connection.refresh_from_db()
        barrier = Barrier(2)
        def action(write):
            try:
                user = UserModel.objects.get(pk=self.one.pk)
                barrier.wait(timeout=10)
                if write:
                    try:
                        set_partner_nickname(user, connection_id=self.connection.pk,
                            expected_revision=self.connection.revision, expected_nickname_revision=1, nickname='Sun')
                        return 'saved'
                    except NicknameConflict:
                        return 'conflict'
                end_connection(connection_id=self.connection.pk,actor=user,expected_revision=self.connection.revision)
                return 'ended'
            finally:
                connections.close_all()
        with patch('social.services.relationship_service.schedule_matrix_room_revocation'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(action, [True,False]))
        self.assertEqual(results[1],'ended')
        self.assertIn(results[0],['saved','conflict'])
        self.assertFalse(CoupleMembershipModel.objects.filter(couple=accepted.couple).exists())
        self.assertEqual(CoupleMembershipModel.all_objects.get(couple=accepted.couple,user=self.one).partner_nickname,'')
