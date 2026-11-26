#!/usr/bin/env python3
"""Disposable loopback HTTP server for paired KMP tests; never uses an app DB."""
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
    parser.add_argument('--moment-video', action='store_true', help='Seed synthetic video and run its isolated worker')
    parser.add_argument('--explore', action='store_true', help='Seed synthetic public discovery, private Faves and follow/privacy HTTP fixtures')
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
    from chat.models import ChatRoom
    from authentication.sessions import create_session
    first = UserModel.objects.create_user(phone_number='+15005550101', full_name='Synthetic A', is_phone_verified=True, is_profile_complete=True)
    second = UserModel.objects.create_user(phone_number='+15005550102', full_name='Synthetic B', is_phone_verified=True, is_profile_complete=True)
    couple_link = CoupleConnectionModel.objects.create(sender_number=first.phone_number, receiver_number=second.phone_number, connection_status='ACCEPTED')
    couple = create_or_get_couple_for_connection(couple_link)
    room = ChatRoom.objects.create(user_one=first, user_two=second, couple=couple)
    visitor = UserModel.objects.create_user(phone_number='+15005550103', full_name='Synthetic Visitor', is_phone_verified=True, is_profile_complete=True)
    profile_viewer = UserModel.objects.create_user(phone_number='+15005550104', full_name='Profile Alex', username='profile_alex', is_phone_verified=True, is_profile_complete=True)
    profile_target = UserModel.objects.create_user(phone_number='+15005550105', full_name='Profile Maya', username='profile_maya', is_phone_verified=True, is_profile_complete=True)
    InterestModel.objects.create(name='Synthetic hiking')
    # Disposable current-session tokens are shared only through a private temp file.
    payload = {
        'base_url': f'http://127.0.0.1:{args.port}/',
        'room_id': str(room.pk),
        'account_id': first.pk,
        'access': create_session(first)['access'],
        'couple_id': couple.pk,
        'partner_id': second.pk,
        'partner_access': create_session(second)['access'],
        'visitor_id': visitor.pk,
        'visitor_access': create_session(visitor)['access'],
        'profile_viewer_id': profile_viewer.pk,
        'profile_viewer_access': create_session(profile_viewer)['access'],
        'profile_target_id': profile_target.pk,
        'profile_target_access': create_session(profile_target)['access'],
    }
    if args.moment_video:
        import subprocess
        import threading
        import time
        import uuid
        from django.core.files.base import ContentFile
        from django.db import close_old_connections
        from social.services.moment_publication import create_draft, publish
        from social.services.moment_video import begin_video, upload_chunk, finish_video, CHUNK_BYTES
        from social.services.moment_video_processing import process_one
        video_file = args.fixture.parent / 'synthetic-video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=360x640:rate=30',
            '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', str(video_file)], check=True)
        draft_id, video_id = uuid.uuid4(), uuid.uuid4()
        create_draft(first, draft_id, couple.pk)
        begin_video(first, draft_id, video_id, video_file.stat().st_size)
        with video_file.open('rb') as stream:
            offset = 0
            while data := stream.read(CHUNK_BYTES):
                upload_chunk(first, draft_id, video_id, offset, ContentFile(data)); offset += len(data)
        finish_video(first, draft_id, video_id)
        process_one()
        moment, _ = publish(first, draft_id, {'expected_revision': 2, 'photo_ids': [video_id],
            'caption': 'Synthetic video verification', 'visibility': 'private'})
        payload.update(video_path=str(video_file), video_id=str(video_id), moment_id=moment.pk)
        def worker():
            while True:
                close_old_connections()
                try:
                    process_one()
                except Exception:
                    pass  # SQLite contention retries; this disposable helper has no external logging.
                time.sleep(.25)
        threading.Thread(target=worker, daemon=True).start()
    if args.explore:
        from explore_integration_fixture import seed_explore
        seed_explore(payload, first, second, visitor, couple)
    descriptor = os.open(args.fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as file: json.dump(payload, file)
    from srisu.asgi import application
    from daphne.server import Server
    Server(application=application, endpoints=[f'tcp:port={args.port}:interface=127.0.0.1'], signal_handlers=False, verbosity=0).run()


if __name__ == '__main__': main()
