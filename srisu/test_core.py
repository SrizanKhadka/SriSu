"""Offline foundation guarantees. Explicitly selected; never imports chat/tests.py."""
import asyncio
import json
from time import time
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from authentication.catalogue import interest_catalogue
from authentication.models import InterestCategory, InterestModel, UserModel
from chat.consumers.chat_consumers import ChatConsumer
from chat.middleware import JwtAuthMiddleware
from chat.models import ChatRoom, MessageModel
from chat.websocket.protocol import CommandBudget, decode_command
from chat.websocket.responses import socket_event
from social.models import SingleConnectionModel, CoupleConnectionModel, CoupleMembershipModel
from social.services.couple_profile_service import create_or_get_couple_for_connection
from srisu.api.cache import PerformanceCache, cache_key
from utils.exception_handlers import custom_exception_handler


def fixtures():
    users = [UserModel.objects.create_user(phone_number=f'+15005550{i:03}', full_name=f'Synthetic {i}', is_phone_verified=True, is_profile_complete=True) for i in range(3)]
    link = SingleConnectionModel.objects.create(sender_number=users[0].phone_number, receiver_number=users[1].phone_number, connection_status='ACCEPTED')
    room = ChatRoom.objects.create(user_one=users[0], user_two=users[1], singles=link)
    return users, link, room


class CoreHttpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.users, self.link, self.room = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.users[0])
        self.headers = {'HTTP_X_SRISU_CONTRACT': 'core-1'}
        self.history = f'/api/chat/rooms/{self.room.pk}/messages/'

    def test_empty_history_and_field_codes_and_correlation(self):
        response = self.client.get(self.history, **self.headers)
        self.assertEqual(response.status_code, 200)
        from tools.check_core_contracts import validate_contract
        validate_contract("history", response.data)
        self.assertEqual(response.data['data']['messages'], [])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        UUID(response['X-Request-ID'])
        request_id = str(uuid4())
        response = self.client.get(self.history, {'limit': 100}, HTTP_X_REQUEST_ID=request_id, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], 'validation_failed')
        validate_contract('httpError', response.data)
        self.assertEqual(response.data['error']['fields']['limit'], ['max_value'])
        self.assertEqual(response.data['request_id'], request_id)
        legacy = self.client.get(self.history, {'limit': 100})
        self.assertIn('error_details', legacy.data)
        self.assertNotIn('error', legacy.data)

    def test_history_is_ordered_paginated_and_actor_scoped(self):
        rows = [MessageModel.objects.create(chat_room=self.room, sender=self.users[0], receiver=self.users[1], text='Synthetic message') for _ in range(3)]
        page = self.client.get(self.history, {'limit': 2}, **self.headers).data['data']
        self.assertEqual([m['id'] for m in page['messages']], [rows[2].id, rows[1].id])
        next_page = self.client.get(self.history, {'limit': 2, 'cursor': page['next_cursor']}, **self.headers).data['data']
        self.assertEqual([m['id'] for m in next_page['messages']], [rows[0].id])
        self.client.force_authenticate(self.users[2])
        self.assertEqual(self.client.get(self.history, **self.headers).status_code, 404)
        self.client.force_authenticate(self.users[0])
        self.link.connection_status = 'BLOCKED'; self.link.save()
        self.assertEqual(self.client.get(self.history, **self.headers).status_code, 404)
        self.assertEqual(self.client.get('/api/chat/rooms/', **self.headers).data['data']['chat_rooms'], [])

    def test_history_query_cost_is_bounded_by_page_not_message_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for _ in range(20):
            MessageModel.objects.create(chat_room=self.room, sender=self.users[0], receiver=self.users[1], text='Synthetic')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.history, {'limit': 20}, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8)

    def test_membership_revocation_denies_couple_history(self):
        connection = CoupleConnectionModel.objects.create(sender_number=self.users[0].phone_number, receiver_number=self.users[1].phone_number, connection_status='ACCEPTED')
        couple = create_or_get_couple_for_connection(connection)
        ChatRoom.objects.filter(pk=self.room.pk).update(couple=couple)
        self.assertEqual(self.client.get(self.history, **self.headers).status_code, 200)
        CoupleMembershipModel.objects.filter(couple=couple, user=self.users[1]).delete()
        self.assertEqual(self.client.get(self.history, **self.headers).status_code, 404)

    def test_room_cursor_bound_to_actor_and_tie_order(self):
        other_link = SingleConnectionModel.objects.create(sender_number=self.users[0].phone_number, receiver_number=self.users[2].phone_number, connection_status='ACCEPTED')
        other = ChatRoom.objects.create(user_one=self.users[0], user_two=self.users[2], singles=other_link)
        ChatRoom.objects.filter(pk=other.pk).update(updated_at=self.room.updated_at)
        first = self.client.get('/api/chat/rooms/', {'limit': 1}, **self.headers).data['data']
        second = self.client.get('/api/chat/rooms/', {'limit': 1, 'cursor': first['next_cursor']}, **self.headers).data['data']
        self.assertNotEqual(first['chat_rooms'][0]['id'], second['chat_rooms'][0]['id'])
        self.client.force_authenticate(self.users[1])
        self.assertEqual(self.client.get('/api/chat/rooms/', {'cursor': first['next_cursor']}, **self.headers).status_code, 400)

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


class CoreSocketTests(TransactionTestCase):
    def setUp(self): self.users, self.link, self.room = fixtures()

    def socket(self, user=None, expiry=None):
        app = ChatConsumer.as_asgi()
        user = self.users[0] if user is None else user
        async def scoped(scope, receive, send):
            await app({**scope, 'user': user, 'access_expires_at': expiry or time() + 60}, receive, send)
        return WebsocketCommunicator(scoped, '/ws/chat/')

    def test_auth_malformed_limits_and_unknown_command(self):
        async def scenario():
            socket = self.socket(expiry=time() - 10)
            connected, code = await socket.connect(); self.assertFalse(connected); self.assertEqual(code, 4401)
            await socket.disconnect()
            socket = self.socket(); self.assertTrue((await socket.connect())[0])
            await socket.send_to(text_data='not json')
            self.assertEqual((await socket.receive_json_from())['error']['code'], 'validation_failed')
            await socket.send_json_to({'action': 'new_future_command', 'request_id': 'test-1', 'payload': {}})
            response = await socket.receive_json_from(); self.assertEqual(response['error']['code'], 'unknown_action'); self.assertEqual(response['request_id'], 'test-1')
            await socket.send_json_to({'action': 'fetch_messages', 'payload': {'chat_room_id': str(self.room.pk), 'limit': 1000}})
            self.assertIn('limit', (await socket.receive_json_from())['error']['fields'])
            await socket.disconnect()
        async_to_sync(scenario)()

    def test_command_persistence_ack_revocation_and_cleanup(self):
        async def scenario():
            socket = self.socket(); self.assertTrue((await socket.connect())[0])
            await socket.send_json_to({'action': 'fetch_messages', 'request_id': 'sub', 'payload': {'chat_room_id': str(self.room.pk)}})
            self.assertEqual((await socket.receive_json_from())['type'], 'success')
            await socket.send_json_to({'action': 'send_message', 'request_id': 'write-1', 'payload': {'chat_room_id': str(self.room.pk), 'text': 'Synthetic text'}})
            frames = [await socket.receive_json_from() for _ in range(3)]
            acknowledgment = next(f for f in frames if f.get('request_id') == 'write-1')
            self.assertEqual(acknowledgment['type'], 'success')
            from tools.check_core_contracts import validate_contract
            validate_contract('socketAck', acknowledgment)
            validate_contract('message', acknowledgment['data']['message'])
            for frame in frames:
                if frame['type'] == 'event': validate_contract('socketEvent', frame)
            self.assertTrue(await database_sync_to_async(MessageModel.objects.filter(pk=acknowledgment['data']['message']['id']).exists)())
            from channels.layers import get_channel_layer
            layer = get_channel_layer()
            await database_sync_to_async(SingleConnectionModel.objects.filter(pk=self.link.pk).update)(connection_status='BLOCKED')
            await layer.group_send(f'chat_room_{self.room.pk}', {'type': 'chat.broadcast', 'room_id': str(self.room.pk), 'payload': socket_event(action='message_created', data={'private': 'must not reach client'})})
            response = await socket.receive_json_from(); self.assertEqual(response['action'], 'access_revoked'); self.assertNotIn('private', str(response))
            await socket.disconnect()
            self.assertFalse(layer.groups.get(f'chat_room_{self.room.pk}'))
            self.assertFalse(layer.groups.get(f'chat_user_{self.users[0].pk}'))
        async_to_sync(scenario)()

    def test_unrelated_account_cannot_subscribe_or_mutate(self):
        async def scenario():
            socket = self.socket(user=self.users[2]); await socket.connect()
            for action, extra in [('fetch_messages', {}), ('send_message', {'text': 'Not allowed'})]:
                await socket.send_json_to({'action': action, 'payload': {'chat_room_id': str(self.room.pk), **extra}})
                self.assertEqual((await socket.receive_json_from())['error']['code'], 'not_found')
            await socket.disconnect()
        async_to_sync(scenario)()
        self.assertFalse(MessageModel.objects.exists())

    def test_header_auth_legacy_adapter_and_origin_guard(self):
        token = str(AccessToken.for_user(self.users[0]))
        app = JwtAuthMiddleware(ChatConsumer.as_asgi())
        async def scenario():
            for path, headers in [('/ws/chat/', [(b'authorization', f'Bearer {token}'.encode())]), (f'/ws/chat/?token={token}', [])]:
                socket = WebsocketCommunicator(app, path, headers=headers)
                self.assertTrue((await socket.connect())[0]); await socket.disconnect()
            socket = WebsocketCommunicator(app, '/ws/chat/', headers=[(b'origin', b'https://untrusted.test')])
            self.assertFalse((await socket.connect())[0]); await socket.disconnect()
        async_to_sync(scenario)()

    def test_frame_and_rate_limits_reject_without_side_effects(self):
        async def scenario():
            socket = self.socket(); await socket.connect()
            await socket.send_to(text_data='x' * (64 * 1024 + 1))
            self.assertEqual((await socket.receive_json_from())['error']['code'], 'payload_too_large')
            with patch('chat.consumers.chat_consumers.CommandBudget.allow', return_value=False):
                await socket.send_json_to({'action': 'send_message', 'request_id': 'limited', 'payload': {'chat_room_id': str(self.room.pk), 'text': 'Synthetic'}})
                response = await socket.receive_json_from()
                self.assertEqual(response['error']['code'], 'rate_limited')
                self.assertEqual(response['request_id'], 'limited')
            await socket.disconnect()
        async_to_sync(scenario)()
        self.assertFalse(MessageModel.objects.exists())

    def test_publication_outage_does_not_reject_committed_message(self):
        from unittest.mock import AsyncMock
        async def scenario():
            socket = self.socket(); await socket.connect()
            from channels.layers import get_channel_layer
            with patch.object(get_channel_layer(), 'group_send', new=AsyncMock(side_effect=OSError('synthetic outage'))):
                await socket.send_json_to({'action': 'send_message', 'request_id': 'committed', 'payload': {'chat_room_id': str(self.room.pk), 'text': 'Synthetic'}})
                response = await socket.receive_json_from()
                self.assertEqual(response['type'], 'success')
            await socket.disconnect()
        async_to_sync(scenario)()
        self.assertEqual(MessageModel.objects.count(), 1)

    def test_token_bucket_uses_controlled_clock(self):
        now = [10.0]
        with override_settings(WS_COMMAND_BURST=2, WS_COMMANDS_PER_SECOND=1):
            budget = CommandBudget(clock=lambda: now[0])
            self.assertTrue(budget.allow()); self.assertTrue(budget.allow()); self.assertFalse(budget.allow())
            now[0] += 1
            self.assertTrue(budget.allow())


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
