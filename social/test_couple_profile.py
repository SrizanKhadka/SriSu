from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Barrier
from uuid import uuid4
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import skipUnlessDBFeature
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase, APITransactionTestCase, APIClient
from social.test_couple_feed import FeedFixtures
from social.models import CoupleStoryAnswerModel, CoupleSongModel, CoupleMembershipModel, SingleConnectionModel


class ProfileFixtures(FeedFixtures):
    def setUp(self):
        super().setUp()
        self.target = self.couple()
        self.first, self.second = list(self.target.members.order_by('id'))
        self.base = f'/api/social/profiles/{self.target.pk}/'
        self.client.force_authenticate(self.first)

    def data(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.data['data']

    def save(self, section, **values):
        values.setdefault('expected_revision', self.data()['revisions'][section])
        return self.client.patch(self.base + f'sections/{section}/', values, format='json')

    def publish(self, sections):
        self.client.force_authenticate(self.first)
        self.assertEqual(self.save('sharing', action='propose', sections=sections).status_code, 200)
        self.client.force_authenticate(self.second)
        self.assertEqual(self.save('sharing', action='approve').status_code, 200)


class CoupleProfileTests(ProfileFixtures, APITestCase):
    def test_history_does_not_prevent_existing_account_deletion(self):
        from social.models import CoupleProfileChangeModel
        self.save('song', title='Shared song')
        actor_id = self.first.pk
        self.first.delete()
        self.assertFalse(CoupleProfileChangeModel.objects.filter(actor_id=actor_id).exists())

    def test_both_members_and_self_endpoint(self):
        for user in (self.first, self.second):
            self.client.force_authenticate(user)
            self.assertTrue(self.data()['can_edit'])
            self.assertEqual(self.client.get('/api/social/profiles/me/').data['data']['id'], self.target.pk)
            self.assertEqual(self.save('song', title='A shared song').status_code, 200)
        self.assertEqual(CoupleSongModel.objects.get().picked_by, self.second)

    def test_visitor_default_private_and_direct_mutations_denied(self):
        self.client.force_authenticate(self.viewer)
        data = self.data()
        self.assertFalse(data['can_edit'])
        self.assertTrue(set(data).isdisjoint({'members','story','song','interests','cover','anniversary_date','revisions','sharing'}))
        for path in ('sections/song/', 'cover/'):
            self.assertEqual(self.client.patch(self.base + path, {}, format='json').status_code, 404)
        for path in ('history/', 'cover-choices/', 'plans/'):
            self.assertEqual(self.client.get(self.base + path).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.base).status_code, 401)

    def test_story_authority_removal_and_unrelated_fields(self):
        self.save('song', title='Keep me')
        self.assertEqual(self.save('story', answers={'how_met':'At a library','first_move':'Both'}).status_code, 200)
        self.client.force_authenticate(self.second)
        self.save('story', answers={'how_met':'Reading'})
        self.client.force_authenticate(self.first)
        self.save('story', answers={'how_met':''})
        self.assertEqual(CoupleStoryAnswerModel.objects.count(), 2)
        self.assertEqual(CoupleSongModel.objects.get().title, 'Keep me')
        self.assertEqual(self.save('story', answers={'how_met':'Changed'}, author_id=self.second.pk).status_code, 400)

    def test_conflicts_only_same_author_section(self):
        revision = self.data()['revisions']['story']
        self.client.force_authenticate(self.second)
        self.save('story', answers={'how_met':'Partner'})
        self.client.force_authenticate(self.first)
        self.assertEqual(self.save('story', expected_revision=revision, answers={'how_met':'Mine'}).status_code, 200)
        self.assertEqual(self.save('story', expected_revision=revision, answers={'how_met':'Stale'}).status_code, 409)
        self.assertEqual(CoupleStoryAnswerModel.objects.get(author=self.first).answer, 'Mine')

    def test_validation_and_interests_only_self(self):
        for section, values in [('story', {'answers':{'unknown':'no'}}), ('story', {'answers':{'how_met':'x'*241}}), ('song',{'title':''}), ('date',{'anniversary_date':(timezone.localdate()+timedelta(days=1)).isoformat()}), ('interests', {'names':['x']*21})]:
            self.assertEqual(self.save(section, **values).status_code, 400)
        self.save('interests', names=['旅行','Coffee'])
        self.client.force_authenticate(self.second)
        self.save('interests', names=['旅行'])
        data=self.data()['interests']
        self.assertEqual(data['shared'], ['旅行'])
        self.assertEqual(set(data['partner']), {'旅行','Coffee'})

    def test_interests_preserve_catalogue_identity_and_removed_rows(self):
        from authentication.models import InterestModel, UserInterestModel
        item = InterestModel.objects.create(name="Reading")
        row = UserInterestModel.objects.create(user=self.first, name="Reading", interest=item)
        self.save('interests', names=['Reading', '旅行'])
        row.refresh_from_db()
        self.assertEqual(row.interest_id, item.pk)
        self.save('interests', names=['旅行'])
        row.refresh_from_db()
        self.assertTrue(row.removed)
        self.assertNotIn('Reading', self.data()['interests']['mine'])

    def test_plan_retry_after_start_is_idempotent(self):
        from unittest.mock import patch
        start = timezone.now() + timedelta(minutes=1)
        body = {'request_id': str(uuid4()), 'title': 'A walk', 'starts_at': start.isoformat()}
        first = self.client.post(self.base+'plans/', body, format='json')
        self.assertEqual(first.status_code, 201)
        with patch('django.utils.timezone.now', return_value=start+timedelta(minutes=2)):
            retry = self.client.post(self.base+'plans/', body, format='json')
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.data['data']['id'], first.data['data']['id'])
            body['request_id'] = str(uuid4())
            self.assertEqual(self.client.post(self.base+'plans/', body, format='json').status_code, 400)

    def test_both_consents_content_bound_and_immediate_revocation(self):
        self.save('song', title='Publish this')
        self.save('sharing', action='propose', sections=['song'])
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('song', self.data())
        self.client.force_authenticate(self.second)
        self.save('sharing', action='approve')
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.data()['song']['title'], 'Publish this')
        self.assertNotIn('members', self.data())
        self.client.force_authenticate(self.first)
        self.save('song', title='New private content')
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('song', self.data())
        self.publish(['song'])
        self.save('sharing', action='revoke')
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('song', self.data())

    def test_membership_and_personal_changes_invalidate_publication(self):
        self.publish(['identity'])
        self.first.full_name='Changed name'
        self.first.save(update_fields=['full_name'])
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('members', self.data())
        self.publish(['identity'])
        membership = self.target.memberships.get(user=self.first)
        position = membership.position
        membership.delete()
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        CoupleMembershipModel.objects.create(couple=self.target,user=self.first,position=position)
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('members', self.data())

    def test_blocking_revocation_and_sensitive_fields(self):
        self.publish(['identity','date','story','interests'])
        self.client.force_authenticate(self.viewer)
        body=self.client.get(self.base).content.decode()
        for value in (self.first.phone_number, 'phone_number', 'email', 'city', 'country', 'history', 'plans'):
            self.assertNotIn(value, body)
        SingleConnectionModel.objects.create(sender_number=self.first.phone_number,receiver_number=self.viewer.phone_number,connection_status='BLOCKED')
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.target.couple_connection.connection_status='BREAK-UP'
        self.target.couple_connection.save()
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.get(self.base+'history/').status_code, 404)

    def image(self):
        value=BytesIO()
        Image.new('RGB',(32,32)).save(value,format='PNG')
        return SimpleUploadedFile('sample.png',value.getvalue(),content_type='image/png')

    def test_cover_validation_replacement_and_guarded_access(self):
        revision=self.data()['revisions']['cover']
        response=self.client.patch(self.base+'cover/', {'expected_revision':revision,'photo':self.image()},format='multipart')
        self.assertEqual(response.status_code,200,response.data)
        self.target.refresh_from_db()
        old=self.target.cover_photo.name
        response=self.client.patch(self.base+'cover/', {'expected_revision':revision,'photo':self.image()},format='multipart')
        self.assertEqual(response.status_code,409)
        self.target.refresh_from_db()
        self.assertEqual(self.target.cover_photo.name,old)
        invalid=SimpleUploadedFile('bad.jpg',b'not an image',content_type='image/jpeg')
        response=self.client.patch(self.base+'cover/', {'expected_revision':self.data()['revisions']['cover'],'photo':invalid},format='multipart')
        self.assertEqual(response.status_code,400)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(self.base+'cover/').status_code,404)
        self.assertEqual(self.client.get('/media/'+old).status_code,404)
        self.publish(['cover'])
        self.client.force_authenticate(self.viewer)
        response=self.client.get(self.base+'cover/')
        self.assertEqual(response.status_code,200)
        b"".join(response.streaming_content)
        self.client.force_authenticate(self.first)
        self.save('sharing',action='revoke')
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(self.base+'cover/').status_code,404)

    def test_cover_cannot_attach_other_couple_media(self):
        from social.models import CoupleMomentPhotoModel
        other=self.couple()
        photo=CoupleMomentPhotoModel.objects.create(moment=self.moment(other),image='couples/moments/synthetic.jpg')
        response=self.client.patch(self.base+'cover/', {'expected_revision':self.data()['revisions']['cover'],'moment_photo_id':photo.pk},format='json')
        self.assertEqual(response.status_code,404)

    def test_plan_idempotency_response_and_permissions(self):
        payload={'request_id':str(uuid4()),'title':'Walk together','starts_at':(timezone.now()+timedelta(days=1)).isoformat()}
        response=self.client.post(self.base+'plans/',payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        plan=response.data['data']
        self.assertEqual(self.client.post(self.base+'plans/',payload,format='json').status_code,200)
        path=self.base+f"plans/{plan['id']}/"
        self.assertEqual(self.client.patch(path,{'expected_revision':1,'response':'yes'},format='json').status_code,400)
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.patch(path,{'expected_revision':1,'response':'yes'},format='json').status_code,200)
        self.assertEqual(self.client.patch(path,{'expected_revision':1,'response':'no'},format='json').status_code,409)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.get(self.base+'plans/').status_code,404)

    def test_encrypted_chat_plan_does_not_create_legacy_plaintext_message(self):
        from chat.models import MessageModel
        before=MessageModel.objects.count()
        payload={'request_id':str(uuid4()),'title':'Synthetic private plan','starts_at':(timezone.now()+timedelta(days=1)).isoformat(),'share_to_legacy_chat':False}
        response=self.client.post(self.base+'plans/',payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.client.post(self.base+'plans/',payload,format='json').status_code,200)
        self.assertEqual(MessageModel.objects.count(),before)

    def test_history_is_bounded_and_private(self):
        from social.models import CoupleProfileChangeModel
        CoupleProfileChangeModel.objects.bulk_create([CoupleProfileChangeModel(couple=self.target,actor=self.first,section='story') for _ in range(23)])
        result=self.client.get(self.base+'history/').data['data']
        self.assertEqual(len(result['results']),20)
        self.assertIsNotNone(result['next_before'])
        self.assertEqual(len(self.client.get(self.base+'history/',{'before':result['next_before']}).data['data']['results']),3)

    def test_live_responses_match_authoritative_schema(self):
        import json
        from tools.check_core_contracts import validate_contract
        validate_contract("coupleProfile", json.loads(self.client.get(self.base).content))
        self.client.force_authenticate(self.viewer)
        validate_contract("coupleProfile", json.loads(self.client.get(self.base).content))

    def test_legacy_paths_also_reject_revoked_members(self):
        self.target.couple_connection.connection_status = 'BREAK-UP'
        self.target.couple_connection.save()
        self.assertEqual(self.client.get('/api/social/couple-profile/').status_code,404)
        self.assertEqual(self.client.patch(f'/api/social/update-couple/{self.target.pk}/',{'title':'No'},format='json').status_code,404)

    def test_stale_revocation_can_only_reduce_visibility(self):
        stale=self.data()['revisions']['sharing']
        self.publish(['identity'])
        self.assertEqual(self.save('sharing', action='revoke', expected_revision=stale).status_code,200)
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('members',self.data())

    def test_question_and_plan_cards_are_private_and_idempotent(self):
        from chat.models import MessageModel
        from chat.presenters.message_presenter import serialize_message_for_socket_sync
        payload={'request_id':str(uuid4()),'prompt':'how_met'}
        url=self.base+'story-invites/'
        response=self.client.post(url,payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.client.post(url,payload,format='json').status_code,200)
        self.assertEqual(MessageModel.objects.count(),1)
        message=MessageModel.objects.get()
        self.assertEqual(message.receiver_id,self.second.pk)
        self.assertEqual(message.profile_action['couple_id'],self.target.pk)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(f'/api/chat/rooms/{message.chat_room_id}/messages/').status_code,404)
        self.assertEqual(self.client.post(url,{'request_id':str(uuid4()),'prompt':'how_met'},format='json').status_code,404)

    def test_failed_storage_preserves_existing_cover(self):
        from unittest.mock import patch
        from social.models import CoupleModel
        storage=CoupleModel._meta.get_field('cover_photo').storage
        revision=self.data()['revisions']['cover']
        self.client.raise_request_exception=False
        with patch.object(storage,'save',side_effect=OSError('synthetic storage failure')):
            response=self.client.patch(self.base+'cover/',{'expected_revision':revision,'photo':self.image()},format='multipart')
        self.assertEqual(response.status_code,500)
        self.target.refresh_from_db()
        self.assertFalse(self.target.cover_photo)
        self.assertEqual(self.data()['revisions']['cover'],revision)

    def test_expired_moment_cover_does_not_extend_media_access(self):
        from social.models import CoupleMomentPhotoModel, CoupleMomentModel
        moment=self.moment(self.target)
        photo=CoupleMomentPhotoModel.objects.create(moment=moment,image='couples/moments/synthetic.jpg')
        self.assertEqual(self.client.patch(self.base+'cover/',{'expected_revision':self.data()['revisions']['cover'],'moment_photo_id':photo.pk},format='json').status_code,200)
        self.publish(['cover'])
        CoupleMomentModel.objects.filter(pk=moment.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.client.force_authenticate(self.viewer)
        self.assertNotIn('cover',self.data())
        self.assertEqual(self.client.get(self.base+'cover/').status_code,404)


class CoupleProfileConcurrencyTests(ProfileFixtures, APITransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_same_section_only_one_wins(self):
        revision=self.data()['revisions']['song']
        barrier=Barrier(2)
        def save(user):
            try:
                client=APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                return client.patch(self.base+'sections/song/',{'expected_revision':revision,'title':'Concurrent song'},format='json').status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,[self.first,self.second]))
        self.assertEqual(sorted(results),[200,409])
