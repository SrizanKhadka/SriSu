"""Nickname alerts use synthetic users and never call the real push provider."""
from datetime import timedelta
from unittest.mock import Mock
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from authentication.models import DeviceSession
from social.models import CoupleMembershipModel, CoupleConnectionModel, SingleConnectionModel
from social.services.relationship_service import accept_connection
from .catalogue import CAPABILITY as CATALOGUE
from .models import Event, Delivery, Installation, Preferences
from .previews import CAPABILITY
from .provider import message
from .tests import Fixtures
from .worker import run_once, eligible

URL = '/api/social/settings/partner-nickname/'
INBOX = '/api/notifications/v1/inbox/'


@override_settings(NOTIFICATIONS_ENABLED=True)
class NicknameNotificationTests(Fixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.bind(version=2, capabilities=[CATALOGUE, CAPABILITY]).status_code, 200)

    def change(self, nickname, *, client=None, revision=None, expected=200, notify=True):
        client = client or self.first_client
        partner = client.get('/api/social/settings/').data['data']['partner']
        response = client.put(URL, {'connection_id':partner['connection_id'],
            'expected_revision':partner['revision'],
            'expected_nickname_revision':revision or partner['nickname_revision'], 'nickname':nickname,
            'notify_partner':notify}, format='json')
        self.assertEqual(response.status_code,expected,response.data)
        return response

    def row(self):
        return Delivery.objects.select_related('event', 'installation__session').filter(event__kind='nickname.changed').latest('event__created_at')

    def preview_url(self, row):
        return f'/api/notifications/v1/deliveries/{row.pk}/preview/'

    def inbox(self, client=None):
        result=(client or self.second_client).get(INBOX)
        self.assertEqual(result.status_code,200,result.data)
        return result.data['data']

    def test_set_reveals_selected_name_only_to_recipient_with_protected_copy_and_tap(self):
        self.change('Sunshine <3 🌻')
        event=Event.objects.get(kind='nickname.changed');row=self.row()
        self.assertEqual(event.recipient_id,self.second.pk)
        self.assertEqual(event.context,{'actor_id':self.first.pk,'action':'set'})
        self.assertNotIn('Sunshine',str(event.__dict__))
        private=self.second_client.get(self.preview_url(row))
        self.assertEqual(private.status_code,200,private.data)
        self.assertIn('no-store',private['Cache-Control'])
        value=private.data['data']
        self.assertEqual(value['title'],f'{self.first.full_name} gave you a nickname')
        self.assertEqual(value['body'],'You’re now “Sunshine <3 🌻” to them.')
        self.assertEqual(value['actor_id'],self.first.pk)
        wire=message(row,audible=False)
        self.assertEqual(wire.data['kind'],'nickname.changed')
        self.assertEqual(wire.data['title'],'A nickname for you')
        self.assertNotIn('Sunshine',str(wire.data));self.assertNotIn(self.first.full_name,str(wire.data))
        self.assertIsNone(wire.notification)
        for client in (self.first_client,self.third_client):
            self.assertEqual(client.get(self.preview_url(row)).status_code,404)
        item=self.inbox()['items'][0]
        self.assertEqual(item['category'],'requests')
        self.assertEqual(item['segments'][0]['user_id'],self.first.pk)
        self.assertTrue(all('user_id' not in part for part in item['segments'][1:]))
        self.assertIn('Sunshine <3 🌻',''.join(part['text'] for part in item['segments']))
        opened=self.second_client.post(INBOX+str(event.pk)+'/open/',{},format='json')
        self.assertEqual(opened.status_code,200,opened.data)
        self.assertEqual(opened.data['data']['destination'],{'kind':'couple','couple_id':self.couple.pk})
        self.assertEqual(self.inbox()['unread_count'],0)
        self.assertIsNone(self.second_client.get('/api/social/settings/').data['data']['partner']['nickname'])
        self.ready();sender=Mock();run_once(sender);sender.assert_called_once()
        self.assertFalse(sender.call_args.kwargs['audible'])

    def test_change_replay_clear_and_supersession_do_not_duplicate_or_relabel_history(self):
        self.change('Sun');first=self.row()
        self.change('Sun',revision=1);self.change('  Sun  ')
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)
        self.change('Moon');second=self.row()
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),2)
        self.assertFalse(eligible(first))
        self.assertEqual(self.second_client.get(self.preview_url(first)).status_code,404)
        self.assertEqual(self.inbox()['unread_count'],1)
        self.assertEqual([item['id'] for item in self.inbox()['items']],[str(second.event_id)])
        self.assertEqual(self.second_client.get(self.preview_url(second)).data['data']['title'],f'{self.first.full_name} changed your nickname')
        self.change(None);self.change('')
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),2)
        self.assertEqual(self.inbox()['items'],[]);self.assertEqual(self.inbox()['unread_count'],0)
        self.assertEqual(self.second_client.get(self.preview_url(second)).status_code,404)
        self.assertEqual(self.second_client.post(INBOX+str(first.event_id)+'/open/',{},format='json').status_code,404)
        self.ready();sender=Mock()
        while run_once(sender): pass
        sender.assert_not_called()

    def test_rollback_invalid_and_stale_writes_cannot_emit_alert(self):
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self.change('Sun');raise ValueError('synthetic rollback')
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.change('Bad\nName',expected=400)
        self.change('Sun');self.change('Moon',revision=1,expected=409)
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)
        self.assertEqual(self.third_client.put(URL, {'connection_id':self.connection.pk,'expected_revision':1,
            'expected_nickname_revision':1,'nickname':'Other'},format='json').status_code,404)
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)

    def test_each_push_opt_out_keeps_current_inbox_without_backfilling_deliveries(self):
        for index,values in enumerate(({'push':False},{'activity':False},{'categories':{'requests':False}})):
            with self.subTest(values=values):
                Preferences.objects.filter(user=self.second).delete()
                Preferences.objects.create(user=self.second,**values)
                self.change(f'Name {index}')
                self.assertFalse(Delivery.objects.filter(event__kind='nickname.changed').exists())
                self.assertEqual(self.inbox()['unread_count'],1)
        Preferences.objects.filter(user=self.second).delete()
        self.assertFalse(Delivery.objects.filter(event__kind='nickname.changed').exists())
        self.change('Latest');row=self.row()
        Preferences.objects.create(user=self.second,categories={'requests':False})
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()
        self.assertEqual(self.inbox()['unread_count'],1)

    def test_capability_platform_permission_and_master_gate_preserve_existing_rules(self):
        for index,changes in enumerate(({'capabilities':[]},{'platform':'ios','capabilities':[CATALOGUE,CAPABILITY]},
                {'platform':'android','permitted':False,'capabilities':[CATALOGUE,CAPABILITY]}),start=3):
            self.assertEqual(self.bind(version=index,**changes).status_code,200)
            self.change(f'Name {index}')
            self.assertFalse(Delivery.objects.filter(event__kind='nickname.changed').exists())
        self.assertEqual(self.bind(version=6,platform='android',permitted=True,capabilities=[CATALOGUE,CAPABILITY]).status_code,200)
        with override_settings(NOTIFICATIONS_ENABLED=False):self.change('Inbox only')
        self.assertFalse(Delivery.objects.filter(event__kind='nickname.changed').exists())
        self.assertEqual(self.inbox()['unread_count'],1)

    def test_no_existing_alias_backfill_and_no_alert_for_initial_empty_value(self):
        self.change('')
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        CoupleMembershipModel.objects.filter(user=self.first).update(partner_nickname='Existing',partner_nickname_revision=2)
        self.change('Existing')
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.assertEqual(self.bind(version=3,capabilities=[CATALOGUE,CAPABILITY]).status_code,200)
        self.assertFalse(Delivery.objects.filter(event__kind='nickname.changed').exists())

    def test_expired_push_does_not_erase_current_inbox_but_sessions_still_authorize(self):
        self.change('Sun');row=self.row()
        Event.objects.filter(pk=row.event_id).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)
        item=self.inbox()['items'][0];self.assertTrue(item['available']);self.assertIn('Sun',item['body'])
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        self.assertIn(self.second_client.get(INBOX).status_code,(401,403))

    def test_block_unlink_and_relink_remove_access_to_prior_alerts(self):
        self.change('Sun');row=self.row()
        block=SingleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number,connection_status='BLOCKED')
        self.assertEqual(self.inbox()['items'],[])
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)
        block.delete()
        result=self.first_client.post('/api/social/settings/disconnect/',{'connection_id':self.connection.pk,
            'expected_revision':self.connection.revision},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.inbox()['items'],[])
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)
        newer=CoupleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number,connection_status='PENDING')
        accept_connection(connection_id=newer.pk,actor=self.second)
        self.assertFalse(any(item['kind']=='nickname.changed' for item in self.inbox()['items']))
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)

    def test_forged_actor_or_audience_never_exposes_chosen_nickname(self):
        self.change('Sun');row=self.row()
        Event.objects.filter(pk=row.event_id).update(context={'actor_id':self.third.pk,'action':'set'})
        self.assertEqual(self.second_client.get(self.preview_url(row)).status_code,404)
        self.assertFalse(self.inbox()['items'][0]['available'])
        self.assertNotIn('Sun',str(self.inbox()))
        Event.objects.filter(pk=row.event_id).update(audience_membership_ids=[0,1])
        self.assertEqual(self.inbox()['items'],[])

    def test_recipient_alias_for_actor_never_rewrites_selected_nickname(self):
        self.change('My Sun',client=self.second_client)
        self.change('Little Moon')
        row=self.row();value=self.second_client.get(self.preview_url(row)).data['data']
        self.assertEqual(value['title'],'My Sun gave you a nickname')
        self.assertEqual(value['body'],'You’re now “Little Moon” to them.')
        item=self.inbox()['items'][0]
        self.assertEqual(item['segments'][0]['text'],'My Sun')
        self.assertNotIn('My Sun',item['body'])

    def test_legacy_payload_and_explicit_false_remain_private_without_notification_backfill(self):
        context=self.first_client.get('/api/social/settings/').data['data']['partner']
        payload={'connection_id':context['connection_id'],'expected_revision':context['revision'],
            'expected_nickname_revision':context['nickname_revision'],'nickname':'Legacy private name'}
        response=self.first_client.put(URL,payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.change('Legacy private name',notify=True)
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.change('Still private',notify=False)
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.change('Shared by new editor',notify=True)
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)
        self.assertIn('Shared by new editor',self.inbox()['items'][0]['body'])
        self.change('Private newer label',notify=False)
        self.assertEqual(Event.objects.filter(kind='nickname.changed').count(),1)
        self.assertEqual(self.inbox()['items'],[])

    def test_notify_partner_requires_a_json_boolean_before_any_mutation(self):
        for value in (1,0,'true','false',None,[],{}):
            with self.subTest(value=value):self.change('Sun',notify=value,expected=400)
        self.assertFalse(Event.objects.filter(kind='nickname.changed').exists())
        self.assertIsNone(self.first_client.get('/api/social/settings/').data['data']['partner']['nickname'])
