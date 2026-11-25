from datetime import timedelta
from uuid import uuid4
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from social.models import ChallengeTemplate, CoupleMomentModel, CoupleMomentNoteModel, SingleConnectionModel
from social.services import challenges, sparks, plans, date_ideas
from .models import Event, Delivery, Installation, Preferences
from .test_matrix_push import MatrixFixtures, SETTINGS

ROOT = '/api/notifications/v1/inbox/'


@override_settings(**SETTINGS)
class InboxTests(MatrixFixtures, APITestCase):
    def image_file(self, label="synthetic.jpg"):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        output = BytesIO()
        Image.new("RGB", (800, 600), "purple").save(output, "JPEG")
        return SimpleUploadedFile(label, output.getvalue(), content_type="image/jpeg")

    def test_visuals_and_bounded_media_reauthorize_without_marking_read(self):
        from io import BytesIO
        from PIL import Image
        from social.models import CoupleMomentPhotoModel
        self.first.profile_photo = self.image_file()
        self.first.save(update_fields=["profile_photo"])
        self.first_client.post('/api/social/couple-moments/', {'couple':self.couple.pk,
            'caption':'Synthetic moment', 'visibility':'private'}, format='json')
        moment = CoupleMomentModel.objects.get()
        CoupleMomentPhotoModel.objects.create(moment=moment, image=self.image_file('moment.jpg'))
        event = Event.objects.get(kind='moment.published')
        item = self.listing()['items'][0]
        self.assertTrue(item['avatar']); self.assertTrue(item['thumbnail'])
        self.assertEqual(item['segments'][0], {'text':self.first.full_name,'bold':True})
        for slot in ('avatar','thumbnail'):
            url = ROOT + f'{event.pk}/media/{slot}/'
            response = self.second_client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Cache-Control'],'private, no-store')
            self.assertEqual(response['Content-Type'],'image/jpeg')
            self.assertLess(len(response.content),256*1024)
            image = Image.open(BytesIO(response.content))
            self.assertLessEqual(max(image.size),160)
            self.assertEqual(self.third_client.get(url).status_code,404)
        event.refresh_from_db();self.assertIsNone(event.inbox_read_at)
        moment.expires_at=timezone.now()-timedelta(seconds=1);moment.save()
        for slot in ('avatar','thumbnail'):
            self.assertEqual(self.second_client.get(ROOT+f'{event.pk}/media/{slot}/').status_code,404)
        item = self.listing()['items'][0]
        self.assertFalse(item['avatar']);self.assertFalse(item['thumbnail'])
        self.assertIsNone(item['actor_name']);self.assertEqual(item['segments'],[])

    def test_spark_copy_only_requests_an_answer_while_it_is_really_needed(self):
        spark,_=sparks.start(self.first,uuid4(),self.couple.pk,'deep-1-v1')
        envelope={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'A'*256,'session_id':'B'*43}
        sparks.submit(self.first,spark.pk,envelope)
        def sentence():
            row=next(r for r in self.listing()['items'] if r['kind']=='spark.submitted')
            return ''.join(s['text'] for s in row['segments'])
        self.assertIn('Answer to see theirs.',sentence())
        sparks.submit(self.second,spark.pk,envelope)
        self.assertNotIn('Answer to see theirs.',sentence())
        self.assertIn('ready to reveal',sentence())

    def test_media_rechecks_block_membership_and_device_session(self):
        from authentication.models import DeviceSession
        self.first.profile_photo = self.image_file()
        self.first.save(update_fields=['profile_photo'])
        self.plan()
        event = Event.objects.get(kind='plan.created')
        url = ROOT + f'{event.pk}/media/avatar/'
        self.assertEqual(self.second_client.get(url).status_code, 200)
        block = SingleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number, connection_status='BLOCKED')
        self.assertEqual(self.second_client.get(url).status_code, 404)
        block.delete()
        Event.objects.filter(pk=event.pk).update(audience_membership_ids=[90001,90002])
        self.assertEqual(self.second_client.get(url).status_code, 404)
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        self.assertIn(self.second_client.get(url).status_code, [401,403])
        self.assertIn(self.client.get(url).status_code, [401,403])

    def test_invitation_avatar_belongs_to_sender_not_current_partner(self):
        from social.models import CoupleConnectionModel
        from .services import emit
        self.third.profile_photo = self.image_file('sender.jpg')
        self.third.save(update_fields=['profile_photo'])
        invite = CoupleConnectionModel.objects.create(sender_number=self.third.phone_number,
            receiver_number=self.second.phone_number, connection_status='PENDING')
        event = emit('invite.received', self.second, str(uuid4()), invite.pk)
        row = self.listing()['items'][0]
        self.assertTrue(row['avatar']);self.assertFalse(row['thumbnail'])
        self.assertEqual(row['actor_name'], self.third.full_name)
        self.assertEqual(self.second_client.get(ROOT+f'{event.pk}/media/avatar/').status_code,200)
        invite.connection_status='REJECTED';invite.save(update_fields=['connection_status'])
        self.assertEqual(self.second_client.get(ROOT+f'{event.pk}/media/avatar/').status_code,404)

    def test_missing_or_invalid_media_is_a_safe_not_found_not_a_broken_inbox(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.first.profile_photo=SimpleUploadedFile('broken.jpg', b'not an image', content_type='image/jpeg')
        self.first.save(update_fields=['profile_photo'])
        self.plan();event=Event.objects.get(kind='plan.created')
        self.assertTrue(self.listing()['items'][0]['available'])
        for slot in ['avatar','thumbnail','unsupported']:
            response=self.second_client.get(ROOT+f'{event.pk}/media/{slot}/')
            self.assertEqual(response.status_code,404)
            self.assertEqual(response['Cache-Control'],'private, no-store')
        self.first.profile_photo.delete(save=True)
        self.assertFalse(self.listing()['items'][0]['avatar'])

    def listing(self, client=None, **query):
        response = (client or self.second_client).get(ROOT, query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.data['data']

    def open(self, event, client=None):
        return (client or self.second_client).post(ROOT + f'{event.pk}/open/', {}, format='json')

    def test_history_survives_push_expiry_and_disabled_permission_and_never_contains_chat(self):
        Preferences.objects.create(user=self.second, push=False, activity=False, categories={'plans':False})
        Installation.objects.all().delete()
        with override_settings(NOTIFICATIONS_ENABLED=False):
            plan = self.plan()
        self.assertFalse(Delivery.objects.exists())
        Event.objects.update(expires_at=timezone.now()-timedelta(hours=2))
        self.post_transaction()
        result = self.listing()
        self.assertEqual(result['unread_count'], 1)
        self.assertEqual([row['kind'] for row in result['items']], ['plan.created'])
        self.assertIn(plan.title, result['items'][0]['body'])
        event = Event.objects.get(kind='plan.created')
        self.assertEqual(self.open(event).data['data']['destination']['plan_id'], plan.pk)
        self.assertEqual(self.listing()['unread_count'], 0)
        self.assertEqual(self.listing(unread='true')['items'], [])

    def test_unrelated_user_revoked_session_block_and_old_membership_never_see_rows(self):
        self.plan(); event = Event.objects.get(kind='plan.created')
        self.assertEqual(self.listing(self.third_client)['items'], [])
        self.assertEqual(self.open(event, self.third_client).status_code, 404)
        self.assertEqual(self.third_client.post(ROOT+'read/', {'ids':[str(event.pk)]}, format='json').status_code,404)
        self.assertIsNone(Event.objects.get(pk=event.pk).inbox_read_at)
        block = SingleConnectionModel.objects.create(sender_number=self.first.phone_number,
            receiver_number=self.second.phone_number, connection_status='BLOCKED')
        self.assertEqual(self.listing()['items'], [])
        self.assertEqual(self.open(event).status_code,404)
        block.delete()
        Event.objects.filter(pk=event.pk).update(audience_membership_ids=[90001,90002])
        self.assertEqual(self.listing()['items'], [])
        from authentication.models import DeviceSession
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        self.assertIn(self.second_client.get(ROOT).status_code,[401,403])

    def test_deleted_private_resource_is_generic_and_can_be_marked_read_without_navigation(self):
        plan = self.plan(); event = Event.objects.get(kind='plan.created'); title = plan.title
        plan.delete()
        item = self.listing()['items'][0]
        self.assertFalse(item['available']); self.assertNotIn(title, str(item))
        opened = self.open(event)
        self.assertEqual(opened.status_code,200)
        self.assertIsNone(opened.data['data']['destination'])
        self.assertEqual(opened.data['data']['unread_count'],0)

    def test_read_snapshot_does_not_mark_new_arrivals_and_retries_are_idempotent(self):
        self.plan(); first = Event.objects.get(kind='plan.created'); token = self.listing()['read_token']
        self.plan(); latest = Event.objects.filter(kind='plan.created').latest('created_at')
        for _ in range(2):
            result = self.second_client.post(ROOT+'read/', {'through':token},format='json')
            self.assertEqual(result.status_code,200,result.data)
            self.assertEqual(result.data['data']['unread_count'],1)
        first.refresh_from_db();latest.refresh_from_db()
        self.assertIsNotNone(first.inbox_read_at);self.assertIsNone(latest.inbox_read_at)
        self.assertEqual(self.third_client.post(ROOT+'read/',{'through':token},format='json').status_code,400)

    def test_pagination_has_stable_ties_and_cursor_is_user_and_filter_bound(self):
        for _ in range(28):self.plan()
        Event.objects.update(created_at=timezone.now()-timedelta(minutes=2))
        page1=self.listing();page2=self.listing(cursor=page1['next_cursor'])
        self.assertEqual(len(page1['items']),25);self.assertEqual(len(page2['items']),3)
        self.assertEqual(len({r['id'] for r in page1['items']+page2['items']}),28)
        self.assertIsNone(page2['next_cursor'])
        self.assertEqual(self.third_client.get(ROOT,{'cursor':page1['next_cursor']}).status_code,400)
        self.assertEqual(self.second_client.get(ROOT,{'cursor':page1['next_cursor'],'unread':'true'}).status_code,400)
        self.assertEqual(self.second_client.get(ROOT,{'cursor':'bad'}).status_code,400)
        self.assertEqual(self.second_client.get(ROOT,{'unread':'maybe'}).status_code,400)
        self.assertEqual(self.second_client.post(ROOT+'read/',{},format='json').status_code,400)

    def test_status_coalesces_latest_and_old_retention_is_not_counted(self):
        for revision,status in enumerate(('Feeling cosy','Thinking of you')):
            response=self.first_client.put('/api/social/couple-status/mine/',{'couple':self.couple.pk,
                'text':status,'expected_revision':revision,'request_id':str(uuid4())},format='json')
            self.assertEqual(response.status_code,200)
        rows=self.listing()['items']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['body'],'Thinking of you')
        Event.objects.update(created_at=timezone.now()-timedelta(days=31))
        self.assertEqual(self.listing()['unread_count'],0)

    def test_completed_challenge_and_revealed_spark_open_current_features(self):
        challenge,_=challenges.start(self.first,uuid4(),self.couple.pk,ChallengeTemplate.objects.filter(published=True).first().pk)
        event=Event.objects.get(kind='challenge.invited')
        challenges.act(self.second,challenge.pk,uuid4(),'accept',1)
        challenges.act(self.first,challenge.pk,uuid4(),'confirm',2)
        challenges.act(self.second,challenge.pk,uuid4(),'confirm',3)
        self.assertEqual(self.open(event).data['data']['destination']['challenge_id'],str(challenge.pk))
        spark,_=sparks.start(self.first,uuid4(),self.couple.pk,'deep-1-v1')
        envelope={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'A'*256,'session_id':'B'*43}
        sparks.submit(self.first,spark.pk,envelope)
        event=Event.objects.get(kind='spark.submitted')
        sparks.submit(self.second,spark.pk,envelope)
        row=next(r for r in self.listing()['items'] if r['id']==str(event.pk))
        self.assertNotIn('your turn',row['title']);self.assertNotIn('ciphertext',str(row))
        self.assertEqual(self.open(event).data['data']['destination']['spark_id'],str(spark.pk))

    def test_expired_moment_hides_caption_but_visitor_reply_still_opens_private_thread(self):
        response=self.first_client.post('/api/social/couple-moments/',{'couple':self.couple.pk,
            'caption':'Synthetic private caption','visibility':'public'},format='json')
        self.assertEqual(response.status_code,201)
        moment=CoupleMomentModel.objects.get()
        self.third_client.post(f'/api/social/couple-moments/{moment.pk}/notes/',{'message':'Synthetic appreciation'},format='json')
        note=CoupleMomentNoteModel.objects.get()
        self.second_client.post(f'/api/social/moment-notes/{note.pk}/replies/',{'message':'Synthetic reply'},format='json')
        moment.expires_at=timezone.now()-timedelta(seconds=1);moment.save()
        row=next(r for r in self.listing()['items'] if r['kind']=='moment.published')
        self.assertFalse(row['available']);self.assertNotIn(moment.caption,str(row))
        result=self.listing(self.third_client)
        self.assertEqual([r['kind'] for r in result['items']],['moment.replied'])
        event=Event.objects.get(kind='moment.replied')
        self.assertEqual(self.open(event,self.third_client).data['data']['destination'],{'kind':'moment_note','note_id':note.pk})
        SingleConnectionModel.objects.create(sender_number=self.third.phone_number,receiver_number=self.first.phone_number,connection_status='BLOCKED')
        self.assertEqual(self.listing(self.third_client)['items'],[])

    def test_withdrawn_idea_and_cancelled_plan_have_suitable_destinations(self):
        idea,_=date_ideas.create(self.first,uuid4(),self.couple.pk,'coffee')
        event=Event.objects.get(kind='idea.created')
        self.assertTrue(self.open(event).data['data']['available'])
        date_ideas.act(self.first,idea.pk,uuid4(),1,'withdraw',{})
        self.assertFalse(self.open(event).data['data']['available'])
        plan=self.plan();event=Event.objects.get(kind='plan.created')
        plans.act(self.first,plan.pk,uuid4(),plan.revision,'cancel')
        self.assertEqual(self.open(event).data['data']['destination']['plan_id'],plan.pk)

    def test_unready_secure_question_does_not_break_the_activity_list(self):
        sparks.start(self.first, uuid4(), self.couple.pk, 'deep-1-v1')
        self.mapping.state = 'pending'
        self.mapping.save(update_fields=['state'])
        row = self.listing()['items'][0]
        self.assertEqual(row['kind'], 'spark.started')
        self.assertFalse(row['available'])
        self.assertEqual(row['body'], 'This update is no longer available.')

    def test_partner_requests_recheck_current_access_and_handled_invites(self):
        from social.models import CoupleConnectionModel
        from .services import emit
        invite = CoupleConnectionModel.objects.create(sender_number=self.third.phone_number,
            receiver_number=self.second.phone_number, connection_status='PENDING')
        event = emit('invite.received', self.second, str(uuid4()), invite.pk)
        self.assertEqual(self.open(event).json()['data']['destination'], {'kind':'invitations'})
        invite.connection_status = 'REJECTED'
        invite.save(update_fields=['connection_status'])
        self.assertFalse(self.open(event).json()['data']['available'])
        self.plan()
        plan_event = Event.objects.get(kind='plan.created')
        sharing = emit('sharing.proposed', self.second, str(uuid4()), self.couple.pk,
            couple=self.couple, audience=plan_event.audience_membership_ids)
        self.assertEqual(self.open(sharing).json()['data']['destination'], {'kind':'sharing', 'couple_id':self.couple.pk})
        self.couple.memberships.filter(user=self.second, ended_at__isnull=True).update(ended_at=timezone.now())
        self.assertEqual(self.open(sharing).status_code, 404)

    def test_wire_contract_and_read_snapshot_remain_consistent(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker, ValidationError
        schema = json.loads((Path(__file__).resolve().parents[1] / 'contracts/notifications-1/inbox.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        def check(name, value):
            Draft202012Validator(schema | {'$ref': '#/$defs/' + name}, format_checker=FormatChecker()).validate(value)
        self.plan()
        result = self.second_client.get(ROOT).json()['data']
        check('page', result)
        event = Event.objects.get(kind='plan.created')
        check('open', self.open(event).json()['data'])
        check('summary', self.second_client.get(ROOT + 'summary/').json()['data'])
        check('read', {'through': result['read_token']})
        with self.assertRaises(ValidationError):
            check('item', result['items'][0] | {'kind': 'matrix.message'})
        with self.assertRaises(ValidationError):
            check('open', {'contract':'notification-inbox-1', 'unread_count':0,
                'available':True, 'destination':{'kind':'matrix_conversation', 'room_id':str(uuid4())}})
        # A client clock cannot manufacture a future event inside its read boundary.
        Event.objects.filter(pk=event.pk).update(created_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.listing()['items'], [])
