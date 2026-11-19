from datetime import datetime, timedelta, timezone as tz
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APITestCase, APIClient
from social import test_sparks as spark_fixtures
from social.models import CoupleStatus, CouplePlanModel, CoupleMembershipModel, DateIdea, SparkSession
from social.services import sparks, date_ideas, plans


class HomeSpaceTests(APITestCase):
    setUp = spark_fixtures.SparkTests.setUp

    def daily(self, zone="Asia/Kathmandu", status=200):
        response = self.client.post('/api/social/sparks/daily/', {'couple': self.couple.pk, 'time_zone': zone}, format='json')
        self.assertEqual(response.status_code, status, response.data)
        if status == 200:
            self.assertIn('no-store', response['Cache-Control'])
        return response.data.get('data', {})

    def idea(self, preset='coffee', key=None, status=201):
        r = self.client.post('/api/social/date-ideas/', {'couple': self.couple.pk, 'request_id': str(key or uuid4()), 'preset': preset}, format='json')
        self.assertEqual(r.status_code, status, r.data)
        return r.data.get('data', {})

    def act(self, idea, action, values=None, key=None, status=200):
        r = self.client.post(f"/api/social/date-ideas/{idea['id']}/actions/", {
            'request_id': str(key or uuid4()), 'expected_revision': idea['revision'], 'action': action, 'values': values or {}}, format='json')
        self.assertEqual(r.status_code, status, r.data)
        return r.data.get('data', {})

    def test_new_responses_match_home_contract_and_automatic_assignment_is_not_partner_activity(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator
        contract = json.loads((Path(__file__).resolve().parents[1] / 'contracts/home-1/openapi.json').read_text())
        def validate(response, schema):
            self.assertIn(response.status_code, (200, 201), response.data)
            Draft202012Validator(contract | {"$ref": "#/components/schemas/" + schema + "Response"}).validate(response.json())
        value = self.daily()
        self.assertIsNone(value['partner_activity_at'])
        self.client.force_authenticate(self.two)
        self.assertIsNone(self.daily()['partner_activity_at'])
        validate(self.client.post('/api/social/sparks/daily/', {'couple': self.couple.pk, 'time_zone': 'UTC'}, format='json'), 'Daily')
        validate(self.client.get('/api/social/couple-status/', {'couple': self.couple.pk}), 'Statuses')
        validate(self.client.put('/api/social/couple-status/mine/', {'couple': self.couple.pk, 'request_id': str(uuid4()), 'expected_revision': 0, 'text': 'Feeling cosy'}, format='json'), 'Status')
        validate(self.client.post('/api/social/date-ideas/', {'couple': self.couple.pk, 'request_id': str(uuid4()), 'preset': 'coffee'}, format='json'), 'Idea')
        validate(self.client.get('/api/social/date-ideas/', {'couple': self.couple.pk}), 'Ideas')
        sparks.submit(self.one, value['session']['id'], spark_fixtures.SparkTests.envelope())
        answered = self.daily()
        self.assertEqual(answered['partner_activity_kind'], 'answered')
        self.assertIsNotNone(answered['partner_activity_at'])
        self.assertEqual(answered['session']['answers'], [])

    def test_daily_is_shared_no_ciphertext_no_publication_and_saved_zone(self):
        one = self.daily()
        self.client.force_authenticate(self.two)
        two = self.daily('America/New_York')
        self.assertEqual(one, two)
        self.assertEqual(two['time_zone'], 'Asia/Kathmandu')
        self.assertEqual(two['session']['answers'], [])
        self.assertEqual(two['session']['card_event_id'], '')
        self.assertEqual(SparkSession.objects.count(), 1)
        self.assertEqual(self.client.get('/api/social/sparks/daily/').status_code, 405)

    def test_carried_skip_holds_remainder_of_day_then_rotates_without_backlog(self):
        first_day = datetime(2026, 11, 1, 12, tzinfo=tz.utc)
        with patch('django.utils.timezone.now', return_value=first_day):
            original = self.daily()['session']
        with patch('django.utils.timezone.now', return_value=first_day + timedelta(days=4)):
            carried = self.daily()
            self.assertTrue(carried['carried'])
            self.assertEqual(carried['session']['id'], original['id'])
            sparks.skip(self.two, original['id'])
            self.assertEqual(self.daily()['session']['state'], 'skipped')
        with patch('django.utils.timezone.now', return_value=first_day + timedelta(days=5)):
            next_question = self.daily()['session']
            self.assertNotEqual(next_question['question_id'], original['question_id'])
        self.assertEqual(SparkSession.objects.count(), 2)

    def test_manual_question_uses_same_slot_and_reveal_consumes_day(self):
        manual, _ = sparks.start(self.one, uuid4(), self.couple.pk, 'playful-2-v1')
        self.assertEqual(self.daily()['session']['id'], str(manual.pk))
        sparks.submit(self.one, manual.pk, spark_fixtures.SparkTests.envelope())
        sparks.submit(self.two, manual.pk, spark_fixtures.SparkTests.envelope('C'))
        current = self.daily()
        self.assertEqual(current['session']['state'], 'revealed')
        self.assertEqual(current['revealed_count'], 1)
        self.assertEqual(current['session']['answers'], [])

    def test_daily_room_epoch_and_access_fail_closed(self):
        first = self.daily()['session']
        self.mapping.membership_epoch += 1; self.mapping.save()
        self.assertNotEqual(self.daily()['session']['id'], first['id'])
        self.client.force_authenticate(self.other); self.daily(status=404)
        self.client.force_authenticate(self.one); self.daily('Not/AZone', status=400)
        self.connection.connection_status = 'BREAK-UP'; self.connection.save()
        self.daily(status=404)
        self.client.force_authenticate(None); self.daily(status=401)

    def test_status_private_expiry_exact_retry_and_revision(self):
        body = {'couple': self.couple.pk, 'request_id': str(uuid4()), 'expected_revision': 0, 'text': 'Feeling cosy'}
        path = '/api/social/couple-status/mine/'
        r = self.client.put(path, body, format='json'); self.assertEqual(r.status_code, 200, r.data)
        original = r.data['data']
        self.assertEqual(self.client.put(path, body, format='json').data['data'], original)
        self.assertEqual(self.client.put(path, body | {'text': 'A little busy'}, format='json').status_code, 409)
        self.client.force_authenticate(self.two)
        self.assertEqual(self.client.get('/api/social/couple-status/', {'couple': self.couple.pk}).data['data']['items'][0]['text'], 'Feeling cosy')
        row = CoupleStatus.objects.get()
        with patch('django.utils.timezone.now', return_value=row.expires_at + timedelta(seconds=1)):
            self.assertEqual(self.client.get('/api/social/couple-status/', {'couple': self.couple.pk}).data['data']['items'][0]['text'], '')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/social/couple-status/', {'couple': self.couple.pk}).status_code, 404)
        self.assertEqual(self.client.put(path, body, format='json').status_code, 404)

    def test_idea_interest_undo_and_plan_conversion_are_distinct_retry_safe(self):
        key = uuid4(); idea = self.idea(key=key)
        self.assertEqual(self.idea(key=key, status=200), idea)
        self.idea('walk', status=409)
        self.client.force_authenticate(self.two)
        self.assertEqual(self.idea(status=200), idea)
        self.assertEqual(DateIdea.objects.get().interested_by, [self.one.pk])
        idea = self.act(idea, 'interest', {'interested': True})
        idea = self.act(idea, 'interest', {'interested': False})
        idea = self.act(idea, 'interest', {'interested': True})
        self.assertEqual(CouplePlanModel.objects.count(), 0)
        values = {'title': 'Coffee together', 'starts_at': (datetime.now(tz.utc)+timedelta(days=2)).isoformat(), 'place': '', 'time_zone': 'UTC'}
        key = uuid4(); planned = self.act(idea, 'plan', values, key=key)
        self.assertEqual(self.act(idea, 'plan', values, key=key), planned)
        self.assertEqual(CouplePlanModel.objects.count(), 1)
        self.assertEqual(CouplePlanModel.objects.get().response, 'pending')
        self.act(idea, 'plan', values, status=409)

    def test_idea_author_only_withdraw_and_membership_replacement(self):
        idea = self.idea()
        self.client.force_authenticate(self.two); self.act(idea, 'withdraw', status=404)
        self.client.force_authenticate(self.other); self.act(idea, 'interest', {'interested': True}, status=404)
        self.assertEqual(self.client.get('/api/social/date-ideas/', {'couple': self.couple.pk}).status_code, 404)
        self.client.force_authenticate(self.one)
        member = self.couple.memberships.get(user=self.two); position = member.position; member.delete()
        CoupleMembershipModel.objects.create(couple=self.couple, user=self.two, position=position)
        self.assertEqual(self.client.get(f"/api/social/date-ideas/{idea['id']}/").status_code, 404)
        self.assertEqual(self.client.get('/api/social/date-ideas/', {'couple': self.couple.pk}).data['data']['items'], [])
        self.idea('walk')

    def test_home_today_precedes_incoming_and_counts_only_incoming(self):
        now = datetime(2026, 11, 9, 10, tzinfo=tz.utc)
        with patch('django.utils.timezone.now', return_value=now):
            today, _ = plans.create(self.one, uuid4(), self.couple.pk, title='Today', starts_at=now+timedelta(hours=1), place='', time_zone='UTC')
            plans.act(self.two, today.pk, uuid4(), today.revision, 'accept')
            incoming, _ = plans.create(self.two, uuid4(), self.couple.pk, title='Request', starts_at=now+timedelta(days=1), place='', time_zone='UTC')
            value = self.client.get('/api/social/plans/home/?time_zone=UTC').data['data']
            self.assertEqual(value['featured_plan']['id'], today.pk)
            self.assertEqual(value['requests'], 1)
            self.client.force_authenticate(self.two)
            self.assertEqual(self.client.get('/api/social/plans/home/').data['data']['requests'], 0)
        with patch('django.utils.timezone.now', return_value=now+timedelta(hours=2)):
            self.client.force_authenticate(self.one)
            value = self.client.get('/api/social/plans/home/?time_zone=UTC').data['data']
            self.assertEqual(value['featured_plan']['id'], today.pk)
        with patch('django.utils.timezone.now', return_value=now+timedelta(days=2)):
            value = self.client.get('/api/social/plans/home/?time_zone=UTC').data['data']
            self.assertIsNone(value['featured_plan'])
            self.assertEqual(value['requests'], 1)
            invitations = self.client.get('/api/social/plans/?mode=invitations').data['data']['items']
            self.assertEqual([item['id'] for item in invitations], [incoming.pk])


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL row locks')
class HomeSpaceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient(); spark_fixtures.SparkTests.setUp(self)
    race = spark_fixtures.SparkConcurrencyTests.race

    def test_two_daily_assignments_share_one_question(self):
        result = self.race(*[lambda user=user: sparks.daily(user, self.couple.pk, 'Asia/Kathmandu') for user in [self.one, self.two]])
        self.assertEqual(result[0]['session']['id'], result[1]['session']['id'])
        self.assertEqual(SparkSession.objects.count(), 1)

    def test_two_conversions_cannot_create_two_plans(self):
        idea, _ = date_ideas.create(self.one, uuid4(), self.couple.pk, 'walk')
        idea = date_ideas.act(self.two, idea.pk, uuid4(), idea.revision, 'interest', {'interested': True})
        values = {'title': 'Walk', 'starts_at': datetime.now(tz.utc)+timedelta(days=1), 'place': '', 'time_zone': 'UTC'}
        def convert(user):
            try: return date_ideas.act(user, idea.pk, uuid4(), idea.revision, 'plan', values)
            except plans.PlanConflict: return 'conflict'
        result = self.race(*[lambda user=user: convert(user) for user in [self.one, self.two]])
        self.assertEqual(result.count('conflict'), 1)
        self.assertEqual(CouplePlanModel.objects.count(), 1)
