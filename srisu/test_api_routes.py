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
