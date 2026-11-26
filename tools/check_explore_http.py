#!/usr/bin/env python3
"""Real authenticated loopback HTTP checks against --explore disposable fixtures.

Reads synthetic tokens only from a private temporary fixture; never prints them.
Mutates only the disposable server, and intentionally withdraws its target last.
"""
import argparse
from contextlib import contextmanager
import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Checks:
    def __init__(self, fixture):
        self.fixture = fixture
        self.base = fixture['base_url']
        parsed = urlsplit(self.base)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Only the disposable loopback server is allowed')
        if not fixture.get('explore_moment_ids'):
            raise ValueError('Start the disposable server with --explore')
        self.origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.roles = {'source': 'access', 'source_partner': 'partner_access', 'target': 'visitor_access',
            'target_partner': 'visitor_partner_access', 'unrelated': 'profile_target_access'}
        self.http = build_opener(ProxyHandler({}), NoRedirect())
        self.records = []

    def call(self, method, path, *, role='source', body=None, expected=200, headers=None):
        url = urljoin(self.base, path)
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port) != self.origin:
            raise ValueError('Response attempted to leave the disposable loopback origin')
        request_headers = {'Accept': 'application/json'}
        if role is not None:
            request_headers['Authorization'] = 'Bearer ' + self.fixture[self.roles[role]]
        if headers:
            request_headers.update(headers)
        payload = None if body is None else json.dumps(body).encode()
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=payload, headers=request_headers, method=method)
        start = time.perf_counter()
        try:
            response = self.http.open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            raw = response.read()
            mime = response.headers.get('Content-Type', '')
            result = json.loads(raw) if 'json' in mime and raw else raw
        self.records.append({'method': method, 'path': parsed.path, 'status': status,
            'elapsed_ms': round((time.perf_counter()-start)*1000, 2)})
        wanted = {expected} if isinstance(expected, int) else set(expected)
        if status not in wanted:
            raise AssertionError(f'{method} {parsed.path}: expected {sorted(wanted)}, got {status}')
        return result.get('data', result) if isinstance(result, dict) else result

    def get(self, path, **kwargs):
        return self.call('GET', path, **kwargs)

    def run(self):
        f = self.fixture; target = f['visitor_couple_id']; source = f['couple_id']
        feed = '/api/social/couple-feed/'
        query = lambda **values: '?' + urlencode(values)
        self.get(feed, role=None, expected=(401, 403))
        self.get(feed, headers={'Authorization': 'Bearer deliberately-invalid-synthetic-token'}, expected=401)
        page = self.get(feed + query(scope='broad', page_size=1))
        ids, seen = [], set()
        while True:
            ids.extend(row['couple']['id'] for row in page['results'])
            link = page.get('next')
            if not link:
                break
            assert link not in seen, 'Pagination loop'; seen.add(link)
            page = self.get(link)
        assert len(ids) == len(set(ids)), 'Repeated couple across pages'
        assert target in ids and f['explore_other_couple_id'] in ids and source not in ids
        searched = self.get(feed + query(scope='broad', q=f['explore_creator_name']))
        assert [r['couple']['id'] for r in searched['results']] == [target]
        card = searched['results'][0]
        assert card['couple']['display_name'] == f['explore_target_name']
        assert card['couple']['shared_interests'] == [f['explore_interest_label']]
        assert card['preview']['media_kind'] in ('photo', 'text', 'video', 'mixed')
        assert 1 <= len(card['previews']) <= 3
        assert self.get(feed + query(scope='broad', q='No such synthetic couple'))['results'] == []
        filtered = self.get(feed + query(scope='broad', interest_id=f['explore_interest_id']))
        assert [r['couple']['id'] for r in filtered['results']] == [target]
        assert self.get(feed + query(scope='broad', q='Nora', interest_id=f['explore_interest_id']))['results'] == []
        self.get(feed + query(interest_id=0), expected=400)
        visible_path = '/api/social/couple-feed/visible/' + query(couples=target, section='global', q=f['explore_creator_name'])
        assert len(self.get(visible_path)['results']) == 1

        fave = f'/api/social/couple-faves/{target}/'
        follow = f'/api/social/couple-follows/{target}/'
        followers = '/api/social/couple-followers/'
        saved = self.call('PUT', fave, body={}, expected=(200, 201))
        assert saved['is_faved'] is True
        assert self.get(fave)['is_faved'] is True
        assert self.get(fave, role='source_partner')['is_faved'] is False
        assert self.get(followers, role='target')['results'] == [], 'Private Fave was disclosed'
        initial = self.get(follow)
        assert initial['can_follow'] is True and initial['is_following'] is False
        assert self.get(follow)['version'] == initial['version'], 'Opening/cancelling confirmation must not mutate'
        self.call('PUT', follow, body={'share_identity': False}, expected=400)
        accepted = self.call('PUT', follow, body={'share_identity': True}, expected=201,
            headers={'If-Match': str(initial['version'])})
        repeated = self.call('PUT', follow, body={'share_identity': True},
            headers={'If-Match': str(accepted['version'])})
        inbound = self.get(followers, role='target')['results']
        assert len(inbound) == 1 and inbound[0]['user_id'] == f['account_id']
        assert inbound[0]['display_name'] == f['account_name']
        assert f['partner_name'] not in str(inbound)
        assert self.get(followers, role='target_partner')['results'] == inbound
        assert self.get(follow, role='source_partner')['is_following'] is False
        assert self.get(followers, role='unrelated')['results'] == []
        self.get(followers + query(couple_id=target), role='unrelated', expected=400)
        self.call('DELETE', follow, headers={'If-Match': str(accepted['version'])}, expected=409)
        removed = self.call('DELETE', follow, headers={'If-Match': str(repeated['version'])})
        assert removed['is_following'] is False
        assert self.get(followers, role='target')['results'] == []
        self.call('PUT', follow, body={'share_identity': True}, headers={'If-Match': str(removed['version'])}, expected=201)
        assert self.get(fave)['is_faved'] is True, 'Following altered a private Fave'

        sequence = '/api/social/couple-moments/sequence/' + query(couple=target, mode='all', page_size=5)
        page = self.get(sequence); moments = list(page['results'])
        while page.get('next'):
            page = self.get(page['next']); moments.extend(page['results'])
        assert [m['id'] for m in moments] == f['explore_moment_ids']
        assert f['explore_private_moment_id'] not in [m['id'] for m in moments]
        assert f['explore_text_moment_id'] in [m['id'] for m in moments[5:]], 'Selected anchor did not require pagination'
        photo_detail = f'/api/social/couple-moments/{f["explore_photo_moment_id"]}/'
        detail = self.get(photo_detail)
        assert detail['creator']['name'] == f['explore_creator_name']
        image_url = detail['photos'][0]['image']
        assert self.get(image_url).startswith(b'\xff\xd8'), 'Real photo bytes were not returned'
        private_url = f'/api/social/couple-moments/{f["explore_private_moment_id"]}/'
        self.get(private_url, expected=404)
        hidden = self.get(f'/api/social/couple-moments/{f["explore_hidden_moment_id"]}/')
        assert hidden['creator']['name'] == 'A SriSu member' and hidden['creator']['photo_url'] is None
        self.get(f'/api/social/couple-moments/{f["explore_hidden_moment_id"]}/creator-photo/', expected=404)

        self.call('POST', photo_detail+'notes/', body={'message': ''}, expected=400)
        self.call('POST', photo_detail+'notes/', role='target', body={'message': 'Synthetic own note'}, expected=400)
        note = self.call('POST', photo_detail+'notes/', body={'message': 'Synthetic appreciation from Alex'}, expected=201)
        note_url = f'/api/social/moment-notes/{note["id"]}/'
        self.get(note_url, role='target')
        self.get(note_url, role='unrelated', expected=404)
        self.call('POST', note_url+'replies/', role='target', body={'message': 'Synthetic reply from Maya'}, expected=201)
        replies = self.get(note_url+'replies/')
        assert len(replies['results']) == 1
        self.call('POST', note_url+'replies/', role='source', body={'message': 'Unauthorized author reply'}, expected=403)

        protected_streams = []; cookie = None
        if f.get('explore_video_id'):
            playback = f'/api/social/moment-videos/{f["explore_video_id"]}/playback/'
            access = self.call('POST', playback, body={})
            cookie = { 'Cookie': access['cookie_name']+'='+access['cookie_value'] }
            master = self.get(access['url'], role=None, headers=cookie).decode()
            variant = next(line.strip() for line in master.splitlines() if line.strip() and not line.startswith('#'))
            variant_url = urljoin(access['url'], variant)
            playlist = self.get(variant_url, role=None, headers=cookie).decode()
            segment = next(line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith('#'))
            segment_url = urljoin(variant_url, segment)
            assert len(self.get(segment_url, role=None, headers=cookie)) > 100
            protected_streams = [access['url'], variant_url, segment_url]

        profile = f'/api/social/profiles/{target}/'
        revision = self.get(profile, role='target')['revisions']['sharing']
        self.call('PATCH', profile+'sections/sharing/', role='target',
            body={'action': 'revoke', 'expected_revision': revision})
        self.get(photo_detail, expected=404); self.get(image_url, expected=404)
        self.call('POST', photo_detail+'notes/', body={'message': 'Cannot reach withdrawn Moment'}, expected=404)
        assert self.get(sequence)['results'] == []
        assert self.get(visible_path)['results'] == []
        assert self.get(feed+query(scope='broad', q=f['explore_creator_name']))['results'] == []
        assert self.get(followers, role='target')['results'] == []
        state = self.get(follow)
        assert state['is_following'] is False and state['can_follow'] is False
        assert self.get(fave)['is_faved'] is True
        self.get(photo_detail, role='target')
        if protected_streams:
            self.call('POST', playback, body={}, expected=404)
            for url in protected_streams:
                self.get(url, role=None, headers=cookie, expected=404)
        revision = self.get(profile, role='target')['revisions']['sharing']
        self.call('PATCH', profile+'sections/sharing/', role='target',
            body={'action': 'propose', 'sections': ['identity', 'interests'], 'expected_revision': revision})
        revision = self.get(profile, role='target_partner')['revisions']['sharing']
        self.call('PATCH', profile+'sections/sharing/', role='target_partner',
            body={'action': 'approve', 'expected_revision': revision})
        self.get(photo_detail, expected=404)
        assert self.get(sequence)['results'] == []
        assert self.get(follow)['is_following'] is False, 'Approval restored an old Follow'
        assert self.get(fave)['is_faved'] is True
        return {'passed': True, 'request_count': len(self.records), 'video_verified': bool(protected_streams),
            'checks': ['JWT authentication', 'Global query/filter/pagination', 'private Faves',
                'explicit individual follow and conditional duplicates', 'member-only followers',
                'selected Moment pagination', 'private media/identity', 'appreciation permissions',
                'Go private media/cookie withdrawal', 'no automatic republication'],
            'requests': self.records}


@contextmanager
def disposable_fixture(video):
    """Reuse the KMP server and guarantee credentials/media disappear on every exit."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='srisu-explore-http-') as temporary:
        directory = Path(temporary)
        fixture = directory / 'fixture.json'
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        command = [sys.executable, 'tools/core_integration_server.py', '--fixture', str(fixture),
            '--port', str(port), '--explore', *(['--moment-video'] if video else [])]
        # Local server diagnostics may contain synthetic media URLs; never emit them.
        with (directory / 'server.log').open('w') as log:
            server = subprocess.Popen(command, cwd=root, stdout=log, stderr=log,
                env={**os.environ, 'TMPDIR': temporary})
            try:
                ready = False
                for _ in range(600):
                    if server.poll() is not None:
                        raise RuntimeError('Disposable backend stopped before it was ready')
                    if fixture.exists():
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                                ready = True
                                break
                        except OSError:
                            pass
                    time.sleep(0.1)
                if not ready:
                    raise RuntimeError('Disposable backend did not start within 60 seconds')
                yield fixture
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


def verify(fixture, report):
    if fixture.stat().st_mode & 0o077:
        raise SystemExit('The synthetic fixture must not be accessible to other users')
    checks = Checks(json.loads(fixture.read_text()))
    try:
        result = checks.run()
    except Exception:
        report.write_text(json.dumps({'passed': False, 'requests': checks.records}, indent=2)+'\n')
        raise
    report.write_text(json.dumps(result, indent=2)+'\n')
    print(f'Live Explore HTTP checks passed: {result["request_count"]} authenticated/authorization requests; real HLS verified={result["video_verified"]}.')
    print('Sanitized report:', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--fixture', type=Path, help='Private fixture from a fresh disposable --explore server')
    mode.add_argument('--spawn', action='store_true', help='Start, check and remove a fresh disposable backend automatically')
    parser.add_argument('--moment-video', action='store_true', help='With --spawn, include genuine synthetic HLS media; requires FFmpeg')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.moment_video and not args.spawn:
        parser.error('--moment-video requires --spawn; existing fixtures determine their own video coverage')
    if args.spawn:
        with disposable_fixture(args.moment_video) as fixture:
            verify(fixture, args.report)
        print('Disposable backend stopped; temporary credentials and media removed.')
    else:
        verify(args.fixture, args.report)


if __name__ == '__main__':
    main()
