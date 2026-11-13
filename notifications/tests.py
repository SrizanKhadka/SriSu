"""Current-domain delivery and authorization, with a synthetic provider only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.cache import cache
from django.db import connections, transaction
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from authentication.models import DeviceSession, UserModel
from authentication.sessions import create_session
from social.models import CoupleConnectionModel, CouplePlanModel
from social.services.couple_profile_service import create_or_get_couple_for_connection
from social.services import plans
from .models import Delivery, Event, Installation, Preferences
from .provider import ProviderFailure, message
from .services import destination, emit, register
from .worker import claim, cleanup, run_once


class Fixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.first, self.second, self.third = [UserModel.objects.create_user(
            phone_number=f'+1500555010{i}', full_name=f'Synthetic {i}',
            is_phone_verified=True, is_profile_complete=True) for i in range(1, 4)]
        for name in ('first', 'second', 'third'):
            user = getattr(self, name)
            session = create_session(user)
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+session['access'])
            setattr(self, name+'_client', client)
            setattr(self, name+'_sid', DeviceSession.objects.get(user=user).pk)
        self.connection = CoupleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number, connection_status='ACCEPTED')
        self.couple = create_or_get_couple_for_connection(self.connection)
        self.registration = dict(installation_id=str(uuid4()), proof='s'*48, version=1,
            platform='android', target_kind='token', target='synthetic-provider-target-only', permitted=True)
        self.assertEqual(self.bind().status_code, 200)
        self.values = dict(title='Synthetic private plan', starts_at=timezone.now()+timedelta(hours=2), place='', time_zone='UTC')

    def bind(self, client=None, **changes):
        return (client or self.second_client).put('/api/notifications/v1/installations/', self.registration|changes, format='json')

    def plan(self, request_id=None):
        return plans.create(self.first, request_id or uuid4(), self.couple.pk, **self.values)[0]

    def ready(self):
        Delivery.objects.update(next_attempt_at=timezone.now()-timedelta(seconds=1))


@override_settings(NOTIFICATIONS_ENABLED=True)
class NotificationTests(Fixtures, APITestCase):
    def test_idempotent_plan_has_private_payload_and_owner_only_destination(self):
        key=uuid4(); plan=self.plan(key); self.plan(key)
        self.assertEqual(Event.objects.count(), 1)
        row=Delivery.objects.get(); wire=message(row, audible=True)
        self.assertEqual(set(wire.data), {'schema','delivery_id','expires_at'})
        self.assertNotIn(plan.title, str(wire.data))
        self.assertEqual(wire.notification.body, 'You have an update. Open SriSu to view it.')
        url=f'/api/notifications/v1/deliveries/{row.pk}/'
        self.assertEqual(self.third_client.get(url).status_code, 404)
        self.assertEqual(self.first_client.get(url).status_code, 404)
        self.assertEqual(self.second_client.get(url).data['data'], {'kind':'plan','couple_id':self.couple.pk,'plan_id':plan.pk})
        plan.cancelled_at=timezone.now(); plan.save()
        self.assertEqual(self.second_client.get(url).status_code, 404)
        self.ready(); sender=Mock(); run_once(sender); sender.assert_not_called()

    def test_current_membership_and_block_rechecked(self):
        from social.models import SingleConnectionModel
        self.plan(); event=Event.objects.get()
        SingleConnectionModel.objects.create(sender_number=self.first.phone_number, receiver_number=self.second.phone_number, connection_status='BLOCKED')
        with self.assertRaises(NotFound): destination(event,self.second,self.second_sid)
        SingleConnectionModel.objects.all().delete()
        member=self.couple.memberships.get(user=self.second); member.ended_at=timezone.now(); member.save()
        with self.assertRaises(NotFound): destination(event,self.second,self.second_sid)

    def test_new_membership_cannot_reuse_old_audience(self):
        self.plan(); event=Event.objects.get()
        event.audience_membership_ids=[0,1]; event.save()
        with self.assertRaises(NotFound): destination(event,self.second,self.second_sid)

    def test_legacy_chat_delivery_is_unavailable_and_no_new_chat_event(self):
        with transaction.atomic():
            self.assertIsNone(emit('chat.message',self.second,'legacy',uuid4()))
        event=Event.objects.create(recipient=self.second,kind='chat.message',source='legacy',resource=str(uuid4()),expires_at=timezone.now()+timedelta(hours=1))
        with self.assertRaises(NotFound): destination(event,self.second,self.second_sid)
        self.assertNotIn('notification-plan-1',self.second_client.get('/api/notifications/v1/preferences/').data['data']['capabilities'])

    def test_binding_proof_and_version_fence_old_callbacks(self):
        self.plan()
        self.assertEqual(self.bind(self.third_client,version=2,proof='x'*48).status_code,409)
        self.assertEqual(self.bind(self.third_client,version=2).status_code,200)
        self.assertEqual(self.bind(version=1).status_code,409)
        self.ready(); sender=Mock();run_once(sender);sender.assert_not_called()
        self.assertEqual(Installation.objects.get().user_id,self.third.pk)

    def test_registered_fid_payload_and_invalid_provider_response(self):
        target='c'+'a'*21
        self.assertEqual(self.bind(version=2,target_kind='fid',target=target).status_code,200)
        self.plan(); wire=message(Delivery.objects.get(),audible=False)
        self.assertEqual(wire.fid,target); self.assertIsNone(wire.token)
        self.ready()
        from firebase_admin.exceptions import InvalidArgumentError
        with patch('notifications.provider.app',return_value=object()),patch('firebase_admin.messaging.send',side_effect=InvalidArgumentError('Synthetic invalid payload')):
            run_once()
        self.assertTrue(Installation.objects.get().active)
        self.assertEqual(Delivery.objects.get().outcome,'configuration')

    def test_deleted_event_after_claim_does_not_crash_worker(self):
        self.plan();self.ready()
        def removed():
            result=claim();Event.objects.all().delete();return result
        sender=Mock()
        with patch('notifications.worker.claim',side_effect=removed):self.assertTrue(run_once(sender))
        sender.assert_not_called()

    def test_late_provider_failure_cannot_retire_new_binding(self):
        self.plan();self.ready()
        def rotated(*args,**kwargs):
            self.assertEqual(self.bind(version=2,target='replacement-synthetic-target').status_code,200)
            raise ProviderFailure('unregistered')
        run_once(rotated)
        self.assertTrue(Installation.objects.get().active)

    def test_activity_preference_and_session_revocation_suppress(self):
        self.plan();self.ready();pref=Preferences.objects.create(user=self.second,activity=False)
        sender=Mock();run_once(sender);sender.assert_not_called()
        pref.activity=True;pref.save();self.plan();self.ready()
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        run_once(sender);sender.assert_not_called()

    def test_retry_bound_and_expired_destination(self):
        self.plan();self.ready();run_once(Mock(side_effect=ProviderFailure('transient',120)))
        row=Delivery.objects.get();self.assertEqual(row.state,'pending')
        self.assertGreater(row.next_attempt_at,timezone.now()+timedelta(seconds=119))
        Event.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()

    def test_dormant_target_erased_and_new_binding_has_no_backlog(self):
        self.plan();Installation.objects.update(updated_at=timezone.now()-timedelta(days=31))
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called();cleanup()
        self.assertEqual(Installation.objects.get().target,'')
        self.assertEqual(self.bind(version=2).status_code,200)
        self.assertFalse(Delivery.objects.filter(state='pending').exists())

    def test_rollback_and_later_permission_never_backfill(self):
        with self.assertRaises(ValueError):
            with transaction.atomic():self.plan();raise ValueError()
        self.assertFalse(Event.objects.exists())
        Installation.objects.update(permitted=False);self.plan()
        self.assertFalse(Delivery.objects.exists())
        self.bind(version=2);self.assertFalse(Delivery.objects.exists())

    def test_preference_conflict_owner_scope_and_explicit_mute_preservation(self):
        path='/api/notifications/v1/preferences/'
        pref=self.second_client.get(path).data['data']
        data=dict(expected_revision=pref['revision'],push=False,chat=True,activity=True,mute='8h')
        response=self.second_client.put(path,data,format='json');self.assertEqual(response.status_code,200)
        self.assertEqual(self.second_client.put(path,data,format='json').status_code,409)
        muted=response.data['data']
        data.update(expected_revision=muted['revision'],push=True,mute='unchanged')
        self.assertEqual(self.second_client.put(path,data,format='json').data['data']['mute_until'],muted['mute_until'])
        self.assertTrue(self.third_client.get(path).data['data']['push'])
        self.assertEqual(self.second_client.put(path,data|{'private':'extra'},format='json').status_code,400)

    def test_current_plan_action_replay_and_superseded_response(self):
        plan=self.plan();key=uuid4();revision=plan.revision
        plans.act(self.second,plan.pk,key,revision,'accept')
        plans.act(self.second,plan.pk,key,revision,'accept')
        event=Event.objects.get(kind='plan.responded')
        self.assertEqual(event.recipient_id,self.first.pk)
        self.assertEqual(destination(event,self.first,self.first_sid)['kind'],'plan')
        plan.refresh_from_db();plans.act(self.first,plan.pk,uuid4(),plan.revision,'cancel')
        with self.assertRaises(NotFound):destination(event,self.first,self.first_sid)

    def test_same_pending_proposal_does_not_create_a_second_alert(self):
        plan=self.plan()
        values=dict(title='Synthetic changed plan',starts_at=self.values['starts_at'],place='',time_zone='UTC')
        plan=plans.act(self.second,plan.pk,uuid4(),plan.revision,'propose',**values)
        plans.act(self.second,plan.pk,uuid4(),plan.revision,'propose',**values)
        self.assertEqual(Event.objects.filter(kind='plan.responded').count(),1)

    def test_sharing_approval_and_changed_content_invalidate_notification(self):
        from social.models import CoupleStoryAnswerModel
        path=f'/api/social/profiles/{self.couple.pk}/'
        revision=self.first_client.get(path).data['data']['revisions']['sharing']
        result=self.first_client.patch(path+'sections/sharing/',dict(expected_revision=revision,action='propose',sections=['story']),format='json')
        self.assertEqual(result.status_code,200)
        event=Event.objects.get(kind='sharing.proposed')
        self.assertEqual(destination(event,self.second,self.second_sid)['kind'],'sharing')
        CoupleStoryAnswerModel.objects.create(couple=self.couple,author=self.first,prompt=CoupleStoryAnswerModel.PROMPTS[0][0],answer='Changed synthetic answer')
        with self.assertRaises(NotFound):destination(event,self.second,self.second_sid)

    def test_invitation_creation_acceptance_and_terminal_state_are_once(self):
        from social.services.relationship_service import create_connection_request,accept_connection
        fourth=UserModel.objects.create_user(phone_number='+15005550104',full_name='Synthetic fourth')
        kwargs=dict(sender_number=self.third.phone_number,receiver_number=fourth.phone_number,actor=self.third)
        request=create_connection_request(**kwargs);create_connection_request(**kwargs)
        event=Event.objects.get(kind='invite.received')
        self.assertEqual(destination(event,fourth,None),{'kind':'invitations'})
        accept_connection(connection_id=request.connection.pk,actor=fourth)
        accept_connection(connection_id=request.connection.pk,actor=fourth)
        self.assertEqual(Event.objects.filter(kind='invite.accepted').count(),1)
        with self.assertRaises(NotFound):destination(event,fourth,None)

    def test_spark_reveal_uses_current_session_and_epoch(self):
        from chat.models import ChatRoom,MatrixRoomMapping,MatrixUserMapping
        from social.services import sparks
        room=ChatRoom.objects.create(couple=self.couple,user_one=self.first,user_two=self.second)
        for user in (self.first,self.second):
            MatrixUserMapping.objects.create(user=user,state='active',matrix_localpart=f'synthetic{user.pk}',matrix_user_id=f'@synthetic{user.pk}:test.invalid')
        mapping=MatrixRoomMapping.objects.create(chat_room=room,state='active',room_alias_localpart=f'synthetic-{room.pk}',matrix_room_id='!synthetic:test.invalid',member_matrix_localparts=[f'synthetic{u.pk}' for u in (self.first,self.second)])
        spark,_=sparks.start(self.first,uuid4(),self.couple.pk,'deep-1-v1')
        envelope={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'A'*256,'session_id':'B'*43}
        sparks.submit(self.second,spark.pk,envelope);self.assertFalse(Event.objects.filter(kind='spark.revealed').exists())
        sparks.submit(self.first,spark.pk,envelope);sparks.submit(self.first,spark.pk,envelope)
        event=Event.objects.get(kind='spark.revealed')
        self.assertEqual(event.recipient_id,self.second.pk)
        self.assertEqual(destination(event,self.second,self.second_sid),{'kind':'spark','spark_id':str(spark.pk)})
        mapping.membership_epoch+=1;mapping.save()
        with self.assertRaises(NotFound):destination(event,self.second,self.second_sid)


@override_settings(NOTIFICATIONS_ENABLED=True)
class NotificationRaceTests(Fixtures,APITransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update_skip_locked')
    def test_duplicate_producer_and_competing_workers(self):
        def concurrent(action):
            barrier=Barrier(2)
            def run():
                try:barrier.wait(timeout=10);return action()
                finally:connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(run) for _ in range(2)]
                return [j.result(timeout=20) for j in jobs]
        key=uuid4();concurrent(lambda:self.plan(key))
        self.assertEqual(Event.objects.count(),1);self.ready()
        self.assertEqual(sum(result is not None for result in concurrent(claim)),1)


class NotificationContractTests(APITestCase):
    def test_current_capabilities_destinations_and_private_payload_schema(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator,FormatChecker,ValidationError
        from .api import preferences_data
        schema=json.loads((Path(__file__).resolve().parents[1]/'contracts/notifications-1/schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        user=UserModel.objects.create_user(phone_number='+15005550109')
        def check(name,value):Draft202012Validator(schema|{'$ref':'#/$defs/'+name},format_checker=FormatChecker()).validate(value)
        check('preferences',preferences_data(Preferences.objects.create(user=user)))
        check('destination',{'kind':'spark','spark_id':str(uuid4())})
        data={'schema':'1','delivery_id':str(uuid4()),'expires_at':'2000000000'}
        check('push_data',data)
        with self.assertRaises(ValidationError):check('push_data',data|{'user_id':1})
