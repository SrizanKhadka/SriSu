#!/usr/bin/env python3
"""Disposable loopback ASGI server for paired KMP tests; never uses an app DB."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'srisu.workspace_test_settings'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    # A test endpoint must never contact an external service.
    original_connect = socket.socket.connect
    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('External network disabled in core integration server')
        return original_connect(sock, address)
    socket.socket.connect = local_connect
    import django
    django.setup()
    from django.test.utils import setup_databases
    setup_databases(verbosity=0, interactive=False)
    from authentication.models import UserModel, InterestModel
    from social.models import CoupleConnectionModel
    from social.services.couple_profile_service import create_or_get_couple_for_connection
    from chat.models import ChatRoom, MessageModel
    from rest_framework_simplejwt.tokens import AccessToken
    first = UserModel.objects.create_user(phone_number='+15005550101', full_name='Synthetic A', is_phone_verified=True, is_profile_complete=True)
    second = UserModel.objects.create_user(phone_number='+15005550102', full_name='Synthetic B', is_phone_verified=True, is_profile_complete=True)
    couple_link = CoupleConnectionModel.objects.create(sender_number=first.phone_number, receiver_number=second.phone_number, connection_status='ACCEPTED')
    couple = create_or_get_couple_for_connection(couple_link)
    room = ChatRoom.objects.create(user_one=first, user_two=second, couple=couple, chat_type='couple')
    visitor = UserModel.objects.create_user(phone_number='+15005550103', full_name='Synthetic Visitor', is_phone_verified=True, is_profile_complete=True)
    MessageModel.objects.create(chat_room=room, sender=second, receiver=first, text='Synthetic baseline')
    InterestModel.objects.create(name='Synthetic hiking')
    # This disposable, short-lived token is shared only through a private temporary file.
    from datetime import timedelta
    token = AccessToken.for_user(first); token.set_exp(lifetime=timedelta(minutes=5))
    payload = {'base_url': f'http://127.0.0.1:{args.port}/', 'room_id': str(room.pk), 'account_id': first.pk, 'access': str(token)}
    partner_token = AccessToken.for_user(second); partner_token.set_exp(lifetime=timedelta(minutes=5))
    visitor_token = AccessToken.for_user(visitor); visitor_token.set_exp(lifetime=timedelta(minutes=5))
    payload.update(couple_id=couple.pk, partner_id=second.pk, partner_access=str(partner_token), visitor_id=visitor.pk, visitor_access=str(visitor_token))
    descriptor = os.open(args.fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as file: json.dump(payload, file)
    from srisu.asgi import application
    from daphne.server import Server
    async def observed(scope, receive, send):
        if scope['type'] == 'websocket':
            print('integration_socket_started', flush=True)
        async def observed_send(event):
            if event['type'] in ('websocket.accept', 'websocket.close'):
                print('integration_' + event['type'] + ':' + str(event.get('code', 0)), flush=True)
            await send(event)
        await application(scope, receive, observed_send)
    Server(application=observed, endpoints=[f'tcp:port={args.port}:interface=127.0.0.1'], signal_handlers=False, verbosity=0).run()


if __name__ == '__main__': main()
