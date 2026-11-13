"""KMP HTTP route coverage and pre-view host rejection; no live provider calls."""
import json
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import resolve


class ClientRouteTests(SimpleTestCase):
    def test_every_published_client_path_and_method_resolves(self):
        routes = json.loads((Path(__file__).resolve().parents[1] /
                             'contracts/core-1/routes.json').read_text())['endpoints']
        for name, route in routes.items():
            with self.subTest(operation=name):
                path = route['path'].replace('{id}', '1').replace(
                    '{uuid}', '12345678-1234-1234-1234-123456789abc')
                match = resolve(path)
                method = route['method'].lower()
                view = match.func.cls
                self.assertIn(method, view.http_method_names)
                if hasattr(match.func, 'actions'):
                    self.assertIn(method, match.func.actions)
                else:
                    self.assertTrue(callable(getattr(view, method, None)))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['localhost', '127.0.0.1'])
    @patch('authentication.otp.request_code')
    def test_unlisted_lan_host_is_rejected_before_otp(self, send):
        response = self.client.post('/api/auth/send-otp/',
                                    data={'phone_number': '+15005550123'},
                                    content_type='application/json', HTTP_HOST='192.0.2.73:8000')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response['Content-Type'].startswith('text/html'))
        send.assert_not_called()

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['localhost', '127.0.0.1', '192.0.2.73'])
    @patch('authentication.otp.request_code', return_value={'challenge_id': 'synthetic'})
    def test_explicit_lan_host_reaches_otp_without_disabling_host_validation(self, send):
        response = self.client.post('/api/auth/send-otp/',
                                    data={'phone_number': '+15005550123'},
                                    content_type='application/json', HTTP_HOST='192.0.2.73:8000')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        send.assert_called_once()
        blocked = self.client.get('/api/auth/interests/', HTTP_HOST='untrusted.example')
        self.assertEqual(blocked.status_code, 400)


from django.test import TestCase
from rest_framework.test import APIClient
from authentication.models import UserModel
from social.models import SingleConnectionModel
from chat.models import ChatRoom


class DatingRetirementTests(TestCase):
    def setUp(self):
        self.actor = UserModel.objects.create_user(phone_number='+15005550901', full_name='Synthetic A')
        self.partner = UserModel.objects.create_user(phone_number='+15005550902', full_name='Synthetic B')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def test_old_actions_are_gone_and_cannot_create_relationships(self):
        for path in ('connect-single/', 'connect-single/1/', 'single-connection/sent-requests/',
                     'single-connection/received-requests/', 'user-suggestions/', 'get-suggestion-profile/'):
            for method in ('get', 'post', 'put', 'patch', 'delete'):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)('/api/social/' + path,
                        {'sender_number': self.actor.phone_number, 'receiver_number': self.partner.phone_number})
                    self.assertEqual(response.status_code, 410)
        self.assertEqual(SingleConnectionModel.objects.count(), 0)

    def test_retired_chat_transport_is_gone_without_deleting_block_policy(self):
        link = SingleConnectionModel.objects.create(sender_number=self.actor.phone_number,
            receiver_number=self.partner.phone_number, connection_status='ACCEPTED')
        self.assertFalse(ChatRoom.objects.filter(
            user_one=self.actor,
            user_two=self.partner,
        ).exists())
        self.assertEqual(self.client.get('/api/chat/rooms/').status_code, 404)
        self.assertEqual(self.client.post('/api/chat/media-upload/', {}).status_code, 404)
        self.assertTrue(SingleConnectionModel.objects.filter(pk=link.pk).exists())

    def test_tombstones_require_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/social/user-suggestions/').status_code, 401)

    def test_preserved_personal_preferences_are_owner_scoped(self):
        from social.models import UserPreferenceModel
        mine = UserPreferenceModel.objects.create(user=self.actor)
        other = UserPreferenceModel.objects.create(user=self.partner)
        response = self.client.get('/api/social/user-preferences/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data], [mine.pk])
        self.assertTrue(UserPreferenceModel.objects.filter(pk=other.pk).exists())

    def test_core_client_receives_retirement_not_cursor_expiry(self):
        response = self.client.get('/api/social/user-suggestions/', HTTP_X_SRISU_CONTRACT='core-1')
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.data['error']['code'], 'feature_retired')
        self.assertFalse(response.data['error']['retryable'])
        from tools.check_core_contracts import validate_contract
        validate_contract('httpError', response.data)
