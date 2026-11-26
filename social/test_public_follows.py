"""Privacy boundaries for explicit individual follows and Go private withdrawal."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from urllib.parse import urlsplit
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.test import skipUnlessDBFeature
from PIL import Image
from rest_framework.test import APITestCase, APITransactionTestCase, APIClient
from rest_framework.exceptions import PermissionDenied
from social.test_couple_feed import FeedFixtures
from social.models import (CouplePublicFollow, CoupleFaveModel, CouplePrivacyConsentModel,
    CoupleMomentPhotoModel, CoupleMomentModel, SingleConnectionModel, MomentVideo, MomentVideoFile)
from social.services.couple_profile_sections import audience, digest, profile_for, update_section
from social.services.discovery_identity import identity_values
from social.services.public_follow_service import follow, FollowConflict
from social.api.moment_videos import COOKIE


class FollowFixtures(FeedFixtures):
    def setUp(self):
        super().setUp()
        self.source = self.couple()
        self.actor, self.spouse = list(self.source.members.order_by('pk'))
        self.actor.full_name = 'Ada'; self.actor.save(update_fields=['full_name'])
        self.spouse.full_name = 'Eli'; self.spouse.save(update_fields=['full_name'])
        self.target = self.couple()
        self.recipient, self.other_recipient = list(self.target.members.order_by('pk'))
        self.approve(self.target)
        self.follow_url = f'/api/social/couple-follows/{self.target.pk}/'
        self.followers_url = '/api/social/couple-followers/'

    def approve(self, couple):
        couple.refresh_from_db()
        members = list(couple.memberships.select_related('user').order_by('position'))
        couple.privacy_proposal_version += 1
        couple.privacy_proposal = {'sections': ['identity'], 'audience': audience(members),
            'fingerprints': {'identity': digest(identity_values(members))}}
        couple.save(update_fields=['privacy_proposal_version', 'privacy_proposal'])
        couple.privacy_consents.all().delete()
        CouplePrivacyConsentModel.objects.bulk_create([CouplePrivacyConsentModel(couple=couple,
            user=m.user, proposal_version=couple.privacy_proposal_version) for m in members])

    def opt_in(self):
        self.client.force_authenticate(self.actor)
        response = self.client.put(self.follow_url, {'share_identity': True}, format='json')
        self.assertIn(response.status_code, [200, 201], response.data)
        return response.data['data']

    def incoming(self, user=None, **query):
        self.client.force_authenticate(user or self.recipient)
        response = self.client.get(self.followers_url, query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.data['data']

    def revoke(self, couple, user):
        self.client.force_authenticate(user)
        response = self.client.patch(f'/api/social/profiles/{couple.pk}/sections/sharing/',
            {'action': 'revoke', 'expected_revision': '0'*64}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    @staticmethod
    def photo(name='synthetic.jpg'):
        output = BytesIO(); Image.new('RGB', (8, 8), '#9988aa').save(output, 'JPEG')
        return SimpleUploadedFile(name, output.getvalue(), content_type='image/jpeg')


class PublicFollowTests(FollowFixtures, APITestCase):
    def test_private_faves_never_become_followers_and_explicit_follow_names_only_actor(self):
        CoupleFaveModel.objects.create(user=self.actor, couple=self.target)
        self.assertEqual(self.incoming()['results'], [])
        self.approve(self.source)
        state = self.opt_in()
        self.assertTrue(state['is_following'])
        rows = self.incoming()['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user_id'], self.actor.pk)
        self.assertEqual(rows[0]['display_name'], 'Ada')
        self.assertNotIn('Eli', str(rows))
        self.assertEqual(self.incoming(self.other_recipient)['results'], rows)
        self.assertEqual(CoupleFaveModel.objects.count(), 1)
        self.client.force_authenticate(self.spouse)
        self.assertFalse(self.client.get(self.follow_url).data['data']['is_following'])

    def test_follow_requires_explicit_acknowledgement_and_current_approved_identity(self):
        self.client.force_authenticate(self.actor)
        for body in [{}, {'share_identity': False}, {'share_identity': True, 'user': self.spouse.pk}]:
            self.assertEqual(self.client.put(self.follow_url, body, format='json').status_code, 400)
        state = self.client.get(self.follow_url).data['data']
        self.assertEqual(state['reason'], 'approve_public_identity')
        self.assertEqual(self.client.put(self.follow_url, {'share_identity': True}, format='json').status_code, 403)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(self.follow_url).data['data']['reason'], 'connect_partner')
        self.assertEqual(self.client.put(self.follow_url, {'share_identity': True}, format='json').status_code, 403)
        self.approve(self.source); self.opt_in()
        self.assertEqual(self.client.put(f'/api/social/couple-follows/{self.source.pk}/', {'share_identity': True}, format='json').status_code, 403)

    def test_private_target_cannot_be_followed_by_known_id(self):
        self.approve(self.source)
        self.revoke(self.target, self.recipient)
        self.client.force_authenticate(self.actor)
        self.assertEqual(self.client.get(self.follow_url).data['data']['reason'], 'unavailable')
        self.assertEqual(self.client.put(self.follow_url, {'share_identity': True}, format='json').status_code, 403)
        self.assertFalse(CouplePublicFollow.objects.exists())

    def test_duplicate_opt_in_and_conditional_remove_do_not_duplicate_or_touch_faves(self):
        self.approve(self.source)
        first = self.opt_in(); created = CouplePublicFollow.objects.get().created_at
        second = self.opt_in()
        self.assertEqual(CouplePublicFollow.objects.count(), 1)
        self.assertEqual(CouplePublicFollow.objects.get().created_at, created)
        stale = self.client.delete(self.follow_url, HTTP_IF_MATCH=str(first['version']))
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(self.client.delete(self.follow_url, HTTP_IF_MATCH=str(second['version'])).data['data']['is_following'] is False)
        self.assertFalse(CouplePublicFollow.objects.exists())
        self.assertEqual(self.incoming()['results'], [])

    def test_incoming_list_cannot_be_requested_for_another_couple_or_unauthenticated(self):
        self.approve(self.source); self.opt_in()
        self.assertEqual(self.incoming(self.actor)['results'], [])
        self.assertIsNone(self.incoming(self.viewer)['couple_id'])
        self.assertEqual(self.client.get(self.followers_url, {'couple_id': self.target.pk}).status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.followers_url).status_code, [401, 403])
        self.assertIn(self.client.get(self.follow_url).status_code, [401, 403])

    def test_blocks_hide_followers_and_state_but_still_allow_removal(self):
        self.approve(self.source); self.opt_in()
        SingleConnectionModel.objects.create(sender_number=self.recipient.phone_number,
            receiver_number=self.actor.phone_number, connection_status='BLOCKED')
        self.assertEqual(self.incoming()['results'], [])
        self.client.force_authenticate(self.actor)
        state = self.client.get(self.follow_url).data['data']
        self.assertFalse(state['is_following']); self.assertFalse(state['can_follow'])
        self.assertEqual(self.client.delete(self.follow_url).status_code, 200)
        self.assertFalse(CouplePublicFollow.objects.exists())

    def test_identity_edit_and_membership_change_hide_old_consent(self):
        self.approve(self.source); self.opt_in()
        self.actor.full_name = 'New consented name'; self.actor.save(update_fields=['full_name'])
        self.assertEqual(self.incoming()['results'], [])
        self.approve(self.source)
        self.assertEqual(self.incoming()['results'], [], 'A new identity proposal must not revive an old opt-in')
        self.opt_in(); self.assertEqual(self.incoming()['results'][0]['display_name'], 'New consented name')
        self.source.couple_connection.connection_status = 'BROKEN_UP'
        self.source.couple_connection.save(update_fields=['connection_status'])
        self.assertEqual(self.incoming()['results'], [])

    def test_go_private_permanently_removes_inbound_and_outbound_follows_not_faves(self):
        self.approve(self.source); self.approve(self.target); self.opt_in()
        self.client.force_authenticate(self.recipient)
        own = self.client.put(f'/api/social/couple-follows/{self.source.pk}/', {'share_identity': True}, format='json')
        self.assertEqual(own.status_code, 201)
        CoupleFaveModel.objects.create(user=self.actor, couple=self.target)
        self.revoke(self.target, self.recipient)
        self.assertFalse(CouplePublicFollow.objects.exists())
        self.assertTrue(CoupleFaveModel.objects.exists())
        self.approve(self.target)
        self.assertEqual(self.incoming()['results'], [])

    def test_follower_pagination_is_requester_bound_and_revocation_filtered(self):
        self.approve(self.source); self.opt_in()
        self.client.force_authenticate(self.spouse)
        self.assertEqual(self.client.put(self.follow_url, {'share_identity': True}, format='json').status_code, 201)
        first = self.incoming(page_size=1)
        self.assertEqual(len(first['results']), 1); self.assertIsNotNone(first['next'])
        path = urlsplit(first['next']); next_path = path.path + '?' + path.query
        self.client.force_authenticate(self.other_recipient)
        self.assertEqual(self.client.get(next_path).status_code, 400)
        self.client.force_authenticate(self.recipient)
        second = self.client.get(next_path)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first['results'][0]['user_id'], second.data['data']['results'][0]['user_id'])
        self.revoke(self.source, self.actor)
        self.assertEqual(self.incoming()['results'], [])

    def test_unapproved_moment_creator_identity_and_photo_are_not_disclosed(self):
        self.actor.profile_photo = self.photo(); self.actor.save(update_fields=['profile_photo'])
        moment = self.moment(self.source)
        detail = f'/api/social/couple-moments/{moment.pk}/'
        photo = detail + 'creator-photo/'
        self.client.force_authenticate(self.viewer)
        data = self.client.get(detail).data
        self.assertEqual(data['creator']['name'], 'A SriSu member')
        self.assertIsNone(data['creator']['photo_url'])
        self.assertEqual(self.client.get(photo).status_code, 404)
        self.approve(self.source)
        data = self.client.get(detail).data
        self.assertEqual(data['creator']['name'], 'Ada')
        self.assertIsNotNone(data['creator']['photo_url'])
        self.assertEqual(self.client.get(photo).status_code, 200)
        self.client.force_authenticate(self.spouse)
        self.assertEqual(self.client.get(detail).data['creator']['name'], 'Ada')

    def test_go_private_withdraws_all_public_media_and_reapproval_never_republishes(self):
        self.approve(self.target)
        moment = self.moment(self.target)
        video = MomentVideo.objects.create(owner=self.recipient, state='ready', source_bytes=8)
        photo = CoupleMomentPhotoModel.objects.create(moment=moment, image=self.photo(), video=video, order=0)
        MomentVideoFile.objects.create(video=video, name='master.m3u8', file=ContentFile(b'#EXTM3U\n', name='master.m3u8'), size=8, digest='a'*64)
        archived = self.moment(self.target, is_archived=True)
        expired = self.moment(self.target, hours=48)
        self.client.force_authenticate(self.viewer)
        detail = f'/api/social/couple-moments/{moment.pk}/'
        photo_url = detail + f'photos/{photo.pk}/'
        playback = f'/api/social/moment-videos/{video.pk}/playback/'
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.get(photo_url).status_code, 200)
        self.assertIn(self.target.pk, [row['couple']['id'] for row in self.get_data()['results']])
        self.assertEqual(len(self.client.get(self.sequence_url, {'couple': self.target.pk, 'mode': 'all'}).data['data']['results']), 1)
        handshake = self.client.post(playback, {}, format='json')
        self.assertEqual(handshake.status_code, 200)
        stream = APIClient(); stream.cookies[COOKIE] = handshake.data['data']['cookie_value']
        stream_url = f'/api/social/moment-video-stream/{video.pk}/master.m3u8'
        self.assertEqual(stream.get(stream_url).status_code, 200)
        self.revoke(self.target, self.recipient)
        self.assertEqual(set(CoupleMomentModel.objects.filter(pk__in=[moment.pk, archived.pk, expired.pk]).values_list('visibility', flat=True)), {'private'})
        self.client.force_authenticate(self.viewer)
        for url in [detail, photo_url, detail+'creator-photo/']:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(playback, {}, format='json').status_code, 404)
        self.assertEqual(stream.get(stream_url).status_code, 404, 'An issued stream cookie cannot bypass revocation')
        self.assertEqual(self.client.get(self.sequence_url, {'couple': self.target.pk, 'mode': 'all'}).data['data']['results'], [])
        self.assertNotIn(self.target.pk, [row['couple']['id'] for row in self.get_data()['results']])
        self.approve(self.target)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.client.force_authenticate(self.recipient)
        self.assertEqual(self.client.get(detail).status_code, 200, 'Private owner access remains intact')
        # Even an already-empty sharing proposal must withdraw later public posts.
        self.revoke(self.target, self.recipient)
        later = self.moment(self.target)
        self.revoke(self.target, self.recipient)
        later.refresh_from_db(); self.assertEqual(later.visibility, 'private')


    def test_public_moment_list_batches_identity_approval_without_revealing_names(self):
        self.moment(self.source)
        self.client.force_authenticate(self.viewer)
        url = '/api/social/couple-moments/'
        with CaptureQueriesContext(connection) as baseline:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
        for _ in range(5):
            self.moment(self.couple())
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(expanded), len(baseline) + 2,
            'Adding couples to one page must not add an identity query group per Moment')
        self.assertTrue(all(item['creator']['name'] == 'A SriSu member' for item in response.data['data']['results']))


    def test_sequence_batches_consent_for_ten_photo_and_video_moments(self):
        def add(index):
            moment = self.moment(self.source)
            video = MomentVideo.objects.create(owner=self.actor, state='ready', source_bytes=8,
                width=640, height=360, duration_ms=1000) if index % 2 else None
            CoupleMomentPhotoModel.objects.create(moment=moment, image=self.photo(), video=video, order=0)
        add(0)
        self.client.force_authenticate(self.viewer)
        params = {'couple': self.source.pk, 'mode': 'all', 'page_size': 10}
        with CaptureQueriesContext(connection) as baseline:
            response = self.client.get(self.sequence_url, params)
            self.assertEqual(response.status_code, 200)
        for index in range(1, 10):
            add(index)
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.sequence_url, params)
            self.assertEqual(response.status_code, 200)
        rows = response.data['data']['results']
        self.assertEqual(len(rows), 10)
        self.assertEqual(sum(row['photos'][0]['video'] is not None for row in rows), 5)
        self.assertTrue(all(row['creator']['name'] == 'A SriSu member' for row in rows))
        self.assertLessEqual(len(expanded), len(baseline) + 2,
            'Ten sequence items must share the identity query group, including videos')
        self.approve(self.source)
        public = self.client.get(self.sequence_url, params).data['data']['results']
        self.assertTrue(all(row['creator']['name'] == 'Ada' for row in public))
        self.actor.full_name = 'Not approved'; self.actor.save(update_fields=['full_name'])
        withdrawn = self.client.get(self.sequence_url, params).data['data']['results']
        self.assertTrue(all(row['creator']['name'] == 'A SriSu member' for row in withdrawn),
            'Request-scoped caching cannot retain stale public identity after an edit')


class PublicFollowConcurrencyTests(FollowFixtures, APITransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_version_concurrent_opt_ins_have_one_winner(self):
        self.approve(self.source)
        barrier = Barrier(2)
        def attempt():
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                try:
                    follow(self.actor, self.target.pk, expected=0)
                    return 'saved'
                except FollowConflict:
                    return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(values, ['saved', 'conflict'])
        self.assertEqual(CouplePublicFollow.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_go_private_serializes_with_opt_in_and_leaves_no_public_edge(self):
        self.approve(self.source)
        barrier = Barrier(2)
        def opt_in():
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                try:
                    follow(self.actor, self.target.pk)
                except PermissionDenied:
                    pass  # Revocation committed first, so the target is unavailable.
            finally:
                connections.close_all()
        def withdraw():
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    target, members, _ = profile_for(self.recipient, self.target.pk, owner=True, lock=True)
                    update_section(target, members, self.recipient, 'sharing', {'action': 'revoke', 'expected_revision': '0'*64})
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(opt_in), pool.submit(withdraw)]
            for future in futures:
                future.result(timeout=15)
        self.assertFalse(CouplePublicFollow.objects.exists())
