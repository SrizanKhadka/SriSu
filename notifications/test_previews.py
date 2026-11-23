"""Preview authorization and copy use synthetic accounts/content only."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from authentication.sessions import create_session
from authentication.models import DeviceSession
from social.models import ChallengeTemplate, CoupleMomentModel, CoupleMomentNoteModel, SingleConnectionModel
from social.services import challenges, sparks, date_ideas, plans
from .test_matrix_push import MatrixFixtures, SETTINGS
from .models import Delivery, Event, Installation, Preferences
from .previews import CAPABILITY, FALLBACKS, text
from .catalogue import CAPABILITY as CATALOGUE, POLICIES
from .provider import message


@override_settings(**SETTINGS)
class PreviewTests(MatrixFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.bind(version=3, capabilities=['matrix-chat-push-1', CATALOGUE, CAPABILITY]).status_code,200)

    def url(self, kind, user=None):
        delivery = Delivery.objects.filter(event__kind=kind, event__recipient=user or self.second).latest("event__created_at")
        return f'/api/notifications/v1/deliveries/{delivery.pk}/preview/'

    def preview(self, kind):
        result=self.second_client.get(self.url(kind))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result['Cache-Control'],'private, no-store')
        return result.data['data']

    def test_every_catalogue_event_has_specific_safe_fallback(self):
        self.assertLessEqual(set(POLICIES)|{'matrix.message'},set(FALLBACKS))
        self.assertEqual(len(set(FALLBACKS[k] for k in POLICIES)),len(POLICIES))
        for title,body in FALLBACKS.values():
            self.assertNotIn('You have an update',body)
            self.assertTrue(title and body)

    def test_chat_preview_has_sender_and_exact_event_but_never_plaintext_or_ciphertext(self):
        self.post_transaction()
        value=self.preview('matrix.message')
        self.assertEqual(value['title'],self.first.full_name)
        self.assertEqual(value['matrix_event_id'],self.event['event_id'])
        self.assertEqual(value['room_id'],str(self.room.pk))
        self.assertNotIn('ciphertext',str(value))
        wire=message(Delivery.objects.get(),audible=True)
        self.assertIsNone(wire.notification)
        self.assertEqual(wire.data['schema'],'2')
        self.assertEqual(wire.data['kind'],'matrix.message')
        self.assertEqual(wire.data['audible'],'true')
        self.assertNotIn(self.first.full_name,str(wire.data))
        self.assertNotIn(self.event['event_id'],str(wire.data))
        self.assertNotIn('synthetic-ciphertext-never-retain',str(wire.data))

    def test_plan_preview_contains_title_time_place_but_provider_does_not(self):
        self.values.update(place='Synthetic cafe',time_zone='Asia/Kathmandu')
        plan=self.plan();value=self.preview('plan.created')
        self.assertIn(self.first.full_name,value['title'])
        for part in [plan.title,plan.place,'Asia/Kathmandu']:self.assertIn(part,value['body'])
        self.assertNotIn(plan.title,str(message(Delivery.objects.get(),audible=True).data))
        plan.cancelled_at=timezone.now();plan.save()
        self.assertEqual(self.second_client.get(self.url('plan.created')).status_code,404)

    def test_moment_challenge_spark_and_idea_details(self):
        r=self.first_client.post('/api/social/couple-moments/',{'couple':self.couple.pk,'caption':'Synthetic sunset story','visibility':'private'},format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.preview('moment.published')['body'],'Synthetic sunset story')
        template=ChallengeTemplate.objects.filter(published=True).first()
        challenge=challenges.start(self.first,uuid4(),self.couple.pk,template.pk)[0]
        self.assertEqual(self.preview('challenge.invited')['body'],challenge.snapshot['title'])
        spark=sparks.start(self.first,uuid4(),self.couple.pk,next(iter(sparks.QUESTIONS)))[0]
        self.assertEqual(self.preview('spark.started')['body'],spark.question)
        Preferences.objects.create(user=self.second,categories={'ideas':True})
        date_ideas.create(self.first,uuid4(),self.couple.pk,'coffee')
        self.assertEqual(self.preview('idea.created')['body'],'Coffee together')

    def test_preview_is_bound_to_exact_session_and_installation_and_preferences(self):
        self.plan();url=self.url('plan.created')
        self.assertEqual(self.first_client.get(url).status_code,404)
        self.assertEqual(self.third_client.get(url).status_code,404)
        session=create_session(self.second);other=APIClient();other.credentials(HTTP_AUTHORIZATION='Bearer '+session['access'])
        self.assertEqual(other.get(url).status_code,404)
        Preferences.objects.create(user=self.second,push=False)
        self.assertEqual(self.second_client.get(url).status_code,404)
        Preferences.objects.filter(user=self.second).update(push=True)
        Installation.objects.update(version=4)
        self.assertEqual(self.second_client.get(url).status_code,404)

    def test_block_revocation_expiry_and_redaction_hide_previews(self):
        self.post_transaction();url=self.url('matrix.message')
        block=SingleConnectionModel.objects.create(sender_number=self.first.phone_number,receiver_number=self.second.phone_number,connection_status='BLOCKED')
        self.assertEqual(self.second_client.get(url).status_code,404);block.delete()
        self.post_transaction('redact',[{'type':'m.room.redaction','room_id':self.mapping.matrix_room_id,'redacts':self.event['event_id']}])
        self.assertEqual(self.second_client.get(url).status_code,404)
        self.plan();url=self.url('plan.created')
        Event.objects.filter(kind='plan.created').update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.second_client.get(url).status_code,404)
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        self.assertIn(self.second_client.get(url).status_code,[401,403])

    def test_legacy_installations_keep_auto_display_payloads(self):
        self.bind(version=4,capabilities=['matrix-chat-push-1'])
        self.post_transaction();wire=message(Delivery.objects.get(),audible=True)
        self.assertEqual(wire.data['schema'],'1')
        self.assertEqual(wire.notification.title,'New message')
        self.assertEqual(wire.android.notification.channel_id,'srisu_updates')

    def test_copy_is_bounded_unicode_and_control_safe(self):
        self.assertEqual(text('<b>Hello</b>\n\u202eWorld\x00'),'Hello World')
        self.assertLessEqual(len(text('🌻'*1000)),240)
        self.assertTrue(text('🌻'*1000).endswith('…'))

    def register_other(self, user, client):
        result = self.bind(client, installation_id=str(uuid4()), target=f'synthetic-target-{user.pk}',
            capabilities=['matrix-chat-push-1', CATALOGUE, CAPABILITY])
        self.assertEqual(result.status_code, 200)

    def test_plan_changes_show_proposed_details_and_cancellation_shows_plan(self):
        self.register_other(self.first, self.first_client)
        plan = self.plan()
        proposed = self.values | {'title': 'Synthetic revised picnic', 'place': 'Synthetic garden'}
        plans.act(self.second, plan.pk, uuid4(), plan.revision, 'propose', **proposed)
        result = self.first_client.get(self.url('plan.responded', self.first)).data['data']
        self.assertIn('suggested a plan change', result['title'])
        self.assertIn(proposed['title'], result['body'])
        self.assertIn(proposed['place'], result['body'])
        plan.refresh_from_db()
        plans.act(self.first, plan.pk, uuid4(), plan.revision, 'cancel')
        self.assertIn(plan.title, self.preview('plan.cancelled')['body'])

    def test_appreciation_and_reply_use_the_actual_author_and_text(self):
        self.register_other(self.third, self.third_client)
        Preferences.objects.create(user=self.second, categories={'appreciations': True})
        result = self.first_client.post('/api/social/couple-moments/',
            {'couple': self.couple.pk, 'caption': 'Synthetic public moment', 'visibility': 'public'}, format='json')
        self.assertEqual(result.status_code, 201)
        moment = CoupleMomentModel.objects.get()
        self.third_client.post(f'/api/social/couple-moments/{moment.pk}/notes/',
            {'message': 'Synthetic appreciation text'}, format='json')
        note = CoupleMomentNoteModel.objects.get()
        value = self.preview('moment.appreciated')
        self.assertIn(self.third.full_name, value['title'])
        self.assertEqual(value['body'], 'Synthetic appreciation text')
        self.second_client.post(f'/api/social/moment-notes/{note.pk}/replies/',
            {'message': 'Synthetic reply text'}, format='json')
        value = self.third_client.get(self.url('moment.replied', self.third)).data['data']
        self.assertIn(self.second.full_name, value['title'])
        self.assertEqual(value['body'], 'Synthetic reply text')
        self.assertEqual(self.first_client.get(self.url('moment.replied', self.third)).status_code, 404)

    def test_status_preview_uses_current_authorized_status_and_clear_hides_it(self):
        from social.api.couple_status import STATUSES
        Preferences.objects.create(user=self.second, categories={'status': True})
        for revision, status in enumerate(list(STATUSES)[:2]):
            self.first_client.put('/api/social/couple-status/mine/', {'couple': self.couple.pk,
                'text': status, 'expected_revision': revision, 'request_id': str(uuid4())}, format='json')
            self.assertEqual(self.preview('status.changed')['body'], status)
        self.first_client.put('/api/social/couple-status/mine/', {'couple': self.couple.pk,
            'text': '', 'expected_revision': 2, 'request_id': str(uuid4())}, format='json')
        self.assertEqual(self.second_client.get(self.url('status.changed')).status_code, 404)

    def test_deleted_resource_race_returns_not_found_without_an_exception_response(self):
        self.plan()
        with patch('notifications.previews.preview', side_effect=CoupleMomentModel.DoesNotExist):
            self.assertEqual(self.second_client.get(self.url('plan.created')).status_code, 404)

    def test_new_payload_and_private_preview_match_contract(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((Path(__file__).resolve().parents[1] / 'contracts/notifications-1/schema.json').read_text())
        self.plan()
        for definition, value in [('push_data_v2', message(Delivery.objects.get(), audible=False).data),
                                  ('preview', self.preview('plan.created'))]:
            Draft202012Validator(schema | {'$ref': f'#/$defs/{definition}'}, format_checker=FormatChecker()).validate(value)
