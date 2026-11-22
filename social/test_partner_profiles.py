from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import UserModel, UserInterestModel, UserPhotoAlbumModel
from social.models import CoupleConnectionModel, SingleConnectionModel
from social.services.partner_profile_service import PROFILE_ACCESS_MAX_AGE


class PartnerProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.viewer = UserModel.objects.create_user(phone_number='+15005550101', full_name='Alex', is_phone_verified=True, is_profile_complete=True)
        self.target = UserModel.objects.create_user(phone_number='+15005550102', full_name='Maya', is_phone_verified=True, is_profile_complete=True, username='maya', bio='Weekend walks and coffee.', email='private@example.test', city='Private city')
        self.outsider = UserModel.objects.create_user(phone_number='+15005550103')
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        self.path = f'/api/social/partner-profiles/{self.target.pk}/'
        UserInterestModel.objects.create(user=self.target, name='Walking')
        UserInterestModel.objects.create(user=self.target, name='Removed interest', removed=True)
        UserPhotoAlbumModel.objects.create(user=self.target, photo='user_album/private.jpg')

    def search_token(self):
        response = self.client.get('/api/social/find-partner/', {'phone_number': self.target.phone_number})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('bio', response.data['data'])
        return response.data['data']['profile_access_token']

    def detail(self, token=None):
        return self.client.get(self.path, HTTP_X_PARTNER_PROFILE_ACCESS=token or '')

    def test_default_private_and_fresh_opt_in_projection(self):
        token = self.search_token()
        response = self.detail(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        from tools.check_core_contracts import validate_contract
        validate_contract('partnerProfile', response.data)
        data = response.data['data']
        self.assertFalse(data['details_visible'])
        self.assertEqual(data['connection_state'], 'available')
        self.assertNotIn('bio', data['user'])
        self.target.share_partner_details = True
        self.target.save()
        data = self.detail(token).data['data']
        self.assertTrue(data['details_visible'])
        validate_contract('partnerProfile', {'data': data})
        self.assertEqual(data['user']['bio'], 'Weekend walks and coffee.')
        self.assertEqual([i['name'] for i in data['user']['user_interests']], ['Walking'])
        self.assertEqual(set(data['user']), {'id', 'full_name', 'username', 'phone_number', 'profile_photo', 'bio', 'user_interests'})
        for private in ['private.jpg', 'private@example.test', 'Private city', 'Removed interest']:
            self.assertNotIn(private, str(data))
        self.target.bio = 'Fresh bio'
        self.target.save()
        self.assertEqual(self.detail(token).data['data']['user']['bio'], 'Fresh bio')
        self.target.share_partner_details = False
        self.target.save()
        self.assertNotIn('bio', self.detail(token).data['data']['user'])

    def test_cannot_enumerate_or_reuse_another_viewers_grant(self):
        self.assertEqual(self.detail().status_code, 404)
        self.assertEqual(self.detail('tampered').status_code, 404)
        token = self.search_token()
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.detail(token).status_code, 404)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(f'/api/social/partner-profiles/{self.outsider.pk}/', HTTP_X_PARTNER_PROFILE_ACCESS=token).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.detail(token).status_code, 401)

    def test_grant_expiry_and_phone_reassignment(self):
        import time
        token = self.search_token()
        with patch('django.core.signing.time.time', return_value=time.time() + PROFILE_ACCESS_MAX_AGE + 2):
            self.assertEqual(self.detail(token).status_code, 404)
        self.target.phone_number = '+15005550104'
        self.target.save()
        self.assertEqual(self.detail(token).status_code, 404)

    def test_blocks_in_either_direction_and_deactivation_hide_profile(self):
        token = self.search_token()
        for sender, receiver in [(self.viewer, self.target), (self.target, self.viewer)]:
            block = SingleConnectionModel.objects.create(sender_number=sender.phone_number, receiver_number=receiver.phone_number, connection_status='BLOCKED')
            self.assertEqual(self.detail(token).status_code, 404)
            self.assertEqual(self.client.get('/api/social/find-partner/', {'phone_number': self.target.phone_number}).status_code, 404)
            block.delete()
        self.target.is_active = False
        self.target.save()
        self.assertEqual(self.detail(token).status_code, 404)

    def test_invitation_access_and_direction_revoke_when_ended(self):
        pair = CoupleConnectionModel.objects.create(sender_number=self.viewer.phone_number, receiver_number=self.target.phone_number, connection_status='PENDING')
        response = self.detail().data['data']
        self.assertEqual(response['connection_state'], 'outgoing')
        self.assertEqual(response['request_id'], pair.pk)
        pair.sender_number, pair.receiver_number = pair.receiver_number, pair.sender_number
        pair.save()
        self.assertEqual(self.detail().data['data']['connection_state'], 'incoming')
        pair.connection_status = 'ACCEPTED'
        pair.save()
        from social.services.couple_profile_service import create_or_get_couple_for_connection
        couple = create_or_get_couple_for_connection(pair)
        response = self.detail().data['data']
        self.assertEqual(response['connection_state'], 'connected')
        self.assertTrue(response['details_visible'])
        self.assertNotIn('user_photos', response['user'])
        from django.utils import timezone
        couple.memberships.filter(user=self.viewer).update(ended_at=timezone.now())
        self.assertEqual(self.detail().status_code, 404)
        pair.connection_status = 'BREAK-UP'
        pair.save()
        self.assertEqual(self.detail().status_code, 404)

    def test_another_connection_disables_connect_without_disclosing_it(self):
        token = self.search_token()
        CoupleConnectionModel.objects.create(sender_number=self.target.phone_number, receiver_number=self.outsider.phone_number, connection_status='PENDING')
        response = self.detail(token).data['data']
        self.assertEqual(response['connection_state'], 'unavailable')
        self.assertIsNone(response['request_id'])
        self.assertNotIn(self.outsider.phone_number, str(response))

    def test_current_couple_takes_priority_over_newer_duplicate_invitation(self):
        from social.services.couple_profile_service import create_or_get_couple_for_connection
        accepted = CoupleConnectionModel.objects.create(sender_number=self.viewer.phone_number, receiver_number=self.target.phone_number, connection_status='ACCEPTED')
        create_or_get_couple_for_connection(accepted)
        CoupleConnectionModel.objects.create(sender_number=self.target.phone_number, receiver_number=self.viewer.phone_number, connection_status='PENDING')
        response = self.detail()
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['connection_state'], 'connected')
        self.assertEqual(data['request_id'], accepted.pk)
        self.assertTrue(data['details_visible'])

    def test_membership_in_another_couple_does_not_authorize_profile_read(self):
        from social.services.couple_profile_service import create_or_get_couple_for_connection
        fourth = UserModel.objects.create_user(phone_number='+15005550104')
        for first, second in [(self.target, self.outsider), (self.viewer, fourth)]:
            accepted = CoupleConnectionModel.objects.create(sender_number=first.phone_number, receiver_number=second.phone_number, connection_status='ACCEPTED')
            create_or_get_couple_for_connection(accepted)
        self.assertEqual(self.detail().status_code, 404)
        # Even a stale accepted row between these people cannot borrow either
        # unrelated couple's memberships to unlock their private details.
        CoupleConnectionModel.objects.create(sender_number=self.viewer.phone_number, receiver_number=self.target.phone_number, connection_status='ACCEPTED')
        self.assertEqual(self.detail().status_code, 404)

    def test_owner_can_opt_in_and_out_via_existing_profile_update(self):
        self.client.force_authenticate(self.target)
        for value in (True, False):
            response = self.client.patch('/api/auth/setup-profile/', {'share_partner_details': value}, format='json')
            self.assertEqual(response.status_code, 200)
            self.target.refresh_from_db()
            self.assertEqual(self.target.share_partner_details, value)
        # A target ID in an owner-only update cannot modify another person's preference.
        self.client.force_authenticate(self.viewer)
        self.client.patch('/api/auth/setup-profile/', {'id': self.target.pk, 'share_partner_details': True}, format='json')
        self.target.refresh_from_db()
        self.assertFalse(self.target.share_partner_details)

    def test_old_clients_omitting_preference_preserve_it(self):
        self.target.share_partner_details = True
        self.target.save()
        self.client.force_authenticate(self.target)
        response = self.client.put('/api/auth/setup-profile/', {'full_name': 'Maya', 'username': 'maya', 'bio': 'Updated'}, format='multipart')
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.share_partner_details)

    def test_detail_is_read_only_and_throttled(self):
        token = self.search_token()
        self.assertEqual(self.client.post(self.path, {}).status_code, 405)
        from social.api.views import PartnerDiscoveryThrottle
        cache.clear()
        with patch.dict(PartnerDiscoveryThrottle.THROTTLE_RATES, {'partner_discovery': '1/min'}):
            self.assertEqual(self.detail(token).status_code, 200)
            self.assertEqual(self.detail(token).status_code, 429)
