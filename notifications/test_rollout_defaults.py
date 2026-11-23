"""Real domain actions reach the worker, private preview and authorized tap by default."""
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from social.models import ChallengeTemplate, CoupleMomentModel, CoupleMomentNoteModel
from social.services import challenges, sparks, date_ideas
from .catalogue import CAPABILITY as CATALOGUE, CATEGORY_DEFAULTS, POLICIES
from .models import Delivery, Event, Preferences
from .previews import CAPABILITY
from .provider import message
from .services import emit
from .test_matrix_push import MatrixFixtures, SETTINGS
from .worker import run_once


@override_settings(**SETTINGS)
class DefaultDeliveryTests(MatrixFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.capabilities = ['matrix-chat-push-1', CATALOGUE, CAPABILITY]
        self.assertEqual(self.bind(version=3, capabilities=self.capabilities).status_code, 200)
        for name in ('first', 'third'):
            self.assertEqual(self.bind(getattr(self, name + '_client'), installation_id=str(uuid4()),
                target='synthetic-provider-target-' + name, capabilities=self.capabilities).status_code, 200)

    def deliver(self, kind, *, recipient='second', body=None, title=None, route=None):
        user = getattr(self, recipient)
        client = getattr(self, recipient + '_client')
        row = Delivery.objects.select_related('event', 'installation').filter(
            event__kind=kind, event__recipient=user).latest('event__created_at')
        # Exercise exactly this domain transition before making its next transition.
        Delivery.objects.filter(state='pending').update(next_attempt_at=timezone.now() + timedelta(hours=1))
        Delivery.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        sender = Mock()
        self.assertTrue(run_once(sender))
        sender.assert_called_once()
        row.refresh_from_db()
        self.assertEqual((row.state, row.outcome), ('sent', 'accepted'))
        if row.event.quiet:
            self.assertFalse(sender.call_args.kwargs['audible'])
        wire = message(row, audible=sender.call_args.kwargs['audible'])
        self.assertEqual(wire.data['schema'], '2')
        self.assertEqual(wire.android.priority, 'high')
        self.assertIsNone(wire.android.collapse_key)
        self.assertIsNone(wire.notification)
        url = f'/api/notifications/v1/deliveries/{row.pk}/'
        preview = client.get(url + 'preview/')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview['Cache-Control'], 'private, no-store')
        details = preview.data['data']
        if body:
            self.assertIn(body, details['body'])
            self.assertNotIn(body, str(wire.data))
        if title:
            self.assertIn(title, details['title'])
        resolved = client.get(url)
        self.assertEqual(resolved.status_code, 200, resolved.data)
        if route:
            self.assertEqual(resolved.data['data']['kind'], route)
        outsider = self.third_client if recipient != 'third' else self.first_client
        self.assertEqual(outsider.get(url).status_code, 404)
        self.assertEqual(outsider.get(url + 'preview/').status_code, 404)
        return row, details

    def test_missing_preferences_enable_all_categories_but_explicit_opt_outs_win(self):
        self.assertTrue(all(CATEGORY_DEFAULTS.values()))
        pref = Preferences.objects.create(user=self.second, categories={})
        for kind, policy in POLICIES.items():
            with self.subTest(kind=kind):
                pref.categories = {policy.category: False}
                pref.save()
                with transaction.atomic():
                    event = emit(kind, self.second, uuid4(), 'synthetic-resource')
                self.assertFalse(Delivery.objects.filter(event=event).exists())
                pref.categories = {}
                pref.save()
                with transaction.atomic():
                    event = emit(kind, self.second, uuid4(), 'synthetic-resource')
                self.assertEqual(Delivery.objects.filter(event=event).count(), 1)
        pref.refresh_from_db()
        self.assertEqual(pref.categories, {})

    def test_status_changes_deliver_within_one_hour_without_retry_or_same_text_duplicates(self):
        path = '/api/social/couple-status/mine/'
        groups = []
        for revision, value in enumerate(('Feeling cosy', 'Thinking of you')):
            payload = dict(couple=self.couple.pk, text=value, expected_revision=revision, request_id=str(uuid4()))
            for _ in range(2):
                self.assertEqual(self.first_client.put(path, payload, format='json').status_code, 200)
            row, _ = self.deliver('status.changed', body=value, route='home')
            groups.append(message(row, audible=False).data['group'])
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(Delivery.objects.filter(event__kind='status.changed').count(), 2)
        self.first_client.put(path, dict(payload, expected_revision=2, request_id=str(uuid4())), format='json')
        self.assertEqual(Event.objects.filter(kind='status.changed').count(), 2)
        self.first_client.put(path, dict(payload, text='', expected_revision=3, request_id=str(uuid4())), format='json')
        self.assertEqual(self.second_client.get(f'/api/notifications/v1/deliveries/{row.pk}/preview/').status_code, 404)

    def test_question_first_answer_and_mutual_reveal_deliver_without_answer_content(self):
        spark, _ = sparks.start(self.first, uuid4(), self.couple.pk, 'deep-1-v1')
        self.deliver('spark.started', body=spark.question, route='spark')
        envelope = {'algorithm': 'm.megolm.v1.aes-sha2', 'ciphertext': 'A' * 256, 'session_id': 'B' * 43}
        sparks.submit(self.first, spark.pk, envelope)
        row, preview = self.deliver('spark.submitted', body=spark.question, route='spark')
        self.assertNotIn(envelope['ciphertext'], str(preview))
        sparks.submit(self.first, spark.pk, envelope)
        self.assertEqual(Event.objects.filter(kind='spark.submitted').count(), 1)
        sparks.submit(self.second, spark.pk, envelope)
        self.deliver('spark.revealed', recipient='first', body=spark.question, route='spark')
        self.assertEqual(self.second_client.get(f'/api/notifications/v1/deliveries/{row.pk}/').status_code, 404)

    def test_challenge_invitation_response_progress_and_completion(self):
        template = ChallengeTemplate.objects.filter(published=True).first()
        challenge, _ = challenges.start(self.first, uuid4(), self.couple.pk, template.pk)
        self.deliver('challenge.invited', body=challenge.snapshot['title'], route='challenge')
        challenges.act(self.second, challenge.pk, uuid4(), 'accept', 1)
        self.deliver('challenge.responded', recipient='first', route='challenge')
        for revision, action, phrase in ((2, 'pause', 'paused their part'), (3, 'resume', 'resumed their part'),
                                        (4, 'confirm', 'finished their part')):
            challenges.act(self.first, challenge.pk, uuid4(), action, revision)
            self.deliver('challenge.progress', title=phrase, route='challenge')
        challenges.act(self.second, challenge.pk, uuid4(), 'confirm', 5)
        self.deliver('challenge.completed', recipient='first', route='challenge')

    def test_moment_heart_appreciation_and_reply_deliver_by_default(self):
        response = self.first_client.post('/api/social/couple-moments/',
            {'couple': self.couple.pk, 'caption': 'Synthetic shared sunset', 'visibility': 'public'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        moment = CoupleMomentModel.objects.get()
        self.deliver('moment.published', body=moment.caption, route='moment')
        self.second_client.put(f'/api/social/couple-moments/{moment.pk}/reaction/', {'reacted': True}, format='json')
        self.deliver('moment.hearted', recipient='first', body=moment.caption, route='moment')
        response = self.third_client.post(f'/api/social/couple-moments/{moment.pk}/notes/',
            {'message': 'Synthetic appreciation'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        note = CoupleMomentNoteModel.objects.get()
        self.deliver('moment.appreciated', recipient='first', body=note.message, route='moment_note')
        self.deliver('moment.appreciated', body=note.message, route='moment_note')
        self.second_client.post(f'/api/social/moment-notes/{note.pk}/replies/',
            {'message': 'Synthetic reply'}, format='json')
        self.deliver('moment.replied', recipient='third', body='Synthetic reply', route='moment_note')

    def test_date_idea_and_mutual_interest_deliver_by_default(self):
        idea, _ = date_ideas.create(self.first, uuid4(), self.couple.pk, 'coffee')
        self.deliver('idea.created', body='Coffee together', route='idea')
        date_ideas.act(self.second, idea.pk, uuid4(), 1, 'interest', {'interested': True})
        self.deliver('idea.interested', recipient='first', body='Coffee together', route='idea')

    def test_opt_out_after_action_suppresses_queued_delivery(self):
        sparks.start(self.first, uuid4(), self.couple.pk, 'deep-1-v1')
        Preferences.objects.create(user=self.second, categories={'questions': False})
        self.ready()
        sender = Mock()
        run_once(sender)
        sender.assert_not_called()
        self.assertEqual(Delivery.objects.get().state, 'suppressed')
