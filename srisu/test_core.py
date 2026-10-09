"""Offline foundation guarantees for the active HTTP/Matrix architecture."""
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient, APIRequestFactory

from authentication.catalogue import interest_catalogue
from authentication.models import InterestCategory, InterestModel, UserModel
from chat.models import ChatRoom
from social.models import CoupleConnectionModel
from social.services.couple_profile_service import create_or_get_couple_for_connection
from srisu.api.cache import PerformanceCache, cache_key
from utils.exception_handlers import custom_exception_handler


def fixtures():
    users = [UserModel.objects.create_user(phone_number=f'+15005550{i:03}', full_name=f'Synthetic {i}', is_phone_verified=True, is_profile_complete=True) for i in range(3)]
    link = CoupleConnectionModel.objects.create(sender_number=users[0].phone_number, receiver_number=users[1].phone_number, connection_status='ACCEPTED')
    room = ChatRoom.objects.create(user_one=users[0], user_two=users[1], couple=create_or_get_couple_for_connection(link))
    return users, link, room


class CoreHttpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.users, self.link, self.room = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.users[0])
        self.headers = {'HTTP_X_SRISU_CONTRACT': 'core-1'}

    def test_retired_django_chat_routes_are_absent(self):
        room = str(self.room.pk)
        checks = (
            ("get", "/api/chat/rooms/"),
            ("get", f"/api/chat/rooms/{room}/messages/"),
            ("post", "/api/chat/media-upload/"),
            ("get", "/api/chat/v2/capabilities/"),
            ("get", f"/api/chat/v2/rooms/{room}/messages/"),
            ("get", f"/api/chat/v2/rooms/{room}/changes/"),
            ("post", f"/api/chat/v2/rooms/{room}/operations/"),
            ("post", f"/api/chat/v2/rooms/{room}/receipts/"),
            ("post", f"/api/chat/v2/rooms/{room}/attachments/"),
        )
        for method, path in checks:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_retired_chat_media_namespaces_are_never_publicly_served(self):
        for path in (
            "/media/chats/media/legacy.jpg",
            "/media/chats_media/legacy.jpg",
            "/media/messages/media/legacy.bin",
            "/media/chat_encrypted/legacy.bin",
            "/MEDIA/CHATS/MEDIA/legacy.jpg",
            "/media/Chats_Media/legacy.jpg",
            "/media/Messages/Media/legacy.bin",
            "/media/Chat_Encrypted/legacy.bin",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_cached_catalogue_query_count_and_committed_invalidation(self):
        category = InterestCategory.objects.create(name='outdoors', label='Outdoors')
        interest = InterestModel.objects.create(name='Walking', category=category)
        with self.assertNumQueries(1): self.assertEqual(interest_catalogue()[0]['name'], 'Walking')
        with self.assertNumQueries(0): self.assertEqual(interest_catalogue()[0]['category']['name'], 'outdoors')
        with self.captureOnCommitCallbacks(execute=True):
            interest.name = 'Hiking'; interest.save()
        self.assertEqual(interest_catalogue()[0]['name'], 'Hiking')
        from tools.check_core_contracts import validate_contract
        validate_contract('interests', self.client.get(reverse('interests')).data)
        try:
            with transaction.atomic():
                interest.name = 'Rolled back'; interest.save()
                raise ValueError('rollback')
        except ValueError:
            pass
        self.assertEqual(interest_catalogue()[0]['name'], 'Hiking')

    def test_core_errors_redact_internals_and_legacy_exception_propagates(self):
        request = APIRequestFactory().get('/', HTTP_X_SRISU_CONTRACT='core-1')
        from rest_framework.request import Request
        response = custom_exception_handler(RuntimeError('synthetic-private-detail'), {'request': Request(request)})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('synthetic-private-detail', str(response.data))
        # exception handler marks atomic request rollback; isolate this unit check.
        transaction.set_rollback(False)
        self.assertIsNone(custom_exception_handler(RuntimeError(), {'request': Request(APIRequestFactory().get('/'))}))


class CoreCacheTests(TestCase):
    def test_scope_namespace_expiry_and_outage(self):
        cache.clear(); now = [100.0]
        performance = PerformanceCache(cache, 'synthetic', ttl=5)
        calls = []
        def load(): calls.append(1); return len(calls)
        with patch('time.time', lambda: now[0]):
            self.assertEqual(performance.read(scope='account-a', resource='1', loader=load), 1)
            self.assertEqual(performance.read(scope='account-b', resource='1', loader=load), 2)
            self.assertEqual(performance.read(scope='account-a', resource='1', loader=load), 1)
            now[0] += 6
            self.assertEqual(performance.read(scope='account-a', resource='1', loader=load), 3)
        broken = Mock(); broken.get_or_set.side_effect = OSError('offline')
        self.assertEqual(PerformanceCache(broken, 'synthetic').read(scope='x', resource='1', loader=lambda: 42), 42)
        self.assertNotIn('account-a', cache_key('synthetic', version=1, scope='account-a', resource='1'))

    def test_late_loader_cannot_repopulate_new_generation(self):
        cache.clear(); performance = PerformanceCache(cache, 'synthetic')
        def old_load():
            with self.captureOnCommitCallbacks(execute=True): performance.invalidate_on_commit(scope='a')
            return 'old'
        performance.read(scope='a', resource='x', loader=old_load)
        self.assertEqual(performance.read(scope='a', resource='x', loader=lambda: 'new'), 'new')


class ContractDefinitionTests(TestCase):
    def test_fixtures_and_server_vocabulary_match_contract(self):
        from tools.check_core_contracts import main, SCHEMA
        from srisu.api.errors import STATUS_CODES
        main()
        self.assertEqual({str(key): value for key, value in STATUS_CODES.items()}, SCHEMA['x-status-codes'])


class DiagnosticTests(TestCase):
    def test_private_messages_exception_details_and_extra_fields_are_not_logged(self):
        import logging
        from srisu.api.logging import CoreJsonFormatter
        record = logging.LogRecord('srisu.api', logging.ERROR, __file__, 1, 'synthetic private %s', ('secret',), None)
        record.token = 'synthetic-secret'
        encoded = CoreJsonFormatter().format(record)
        self.assertNotIn('private', encoded)
        self.assertNotIn('secret', encoded)
        self.assertNotIn('token', encoded)
