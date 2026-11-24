from datetime import timedelta
from pathlib import Path
import shutil
import subprocess
import tempfile
import io
import os
import time
from uuid import uuid4
from unittest.mock import patch

from django.core import signing
from django.core.management import call_command, CommandError
from django.test import override_settings, SimpleTestCase
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient, APITransactionTestCase

from social.test_moments import MomentAPITests
from social.models import MomentVideo, MomentVideoFile, MomentDraftPhoto, CoupleMomentModel, CoupleMembershipModel, SingleConnectionModel
from authentication.models import DeviceSession
from social.services.moment_video import CHUNK_BYTES, MAX_BYTES
from social.services.moment_video_processing import process_one, encode
from social.api.moment_videos import COOKIE, SALT


class MomentVideoWorkerCommandTests(SimpleTestCase):
    command_module = "social.management.commands.process_moment_videos."

    def test_missing_media_tools_fail_before_claiming_any_video(self):
        with patch(self.command_module+"check_video_tools",side_effect=FileNotFoundError()), \
                patch(self.command_module+"process_one") as process:
            with self.assertRaisesRegex(CommandError,"Rebuild the backend image"):
                call_command("process_moment_videos",watch=True,stdout=io.StringIO())
            process.assert_not_called()

    def test_preflight_checks_tools_without_processing_jobs(self):
        with patch(self.command_module+"check_video_tools") as check, \
                patch(self.command_module+"process_one") as process:
            call_command("process_moment_videos",check=True,stdout=io.StringIO())
            check.assert_called_once()
            process.assert_not_called()

    def test_worker_health_reports_missing_fresh_and_stale_heartbeats(self):
        with tempfile.TemporaryDirectory() as directory:
            heartbeat=Path(directory)/"heartbeat"
            with override_settings(MOMENT_VIDEO_HEARTBEAT_PATH=str(heartbeat)):
                with self.assertRaises(CommandError):
                    call_command("process_moment_videos",healthcheck=True,stdout=io.StringIO())
                heartbeat.touch()
                call_command("process_moment_videos",healthcheck=True,stdout=io.StringIO())
                os.utime(heartbeat,(time.time()-301,)*2)
                with self.assertRaises(CommandError):
                    call_command("process_moment_videos",healthcheck=True,stdout=io.StringIO())

    def test_watch_updates_health_and_cleans_it_on_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            heartbeat=Path(directory)/"heartbeat"
            def process():
                self.assertTrue(heartbeat.is_file())
                raise KeyboardInterrupt()
            with override_settings(MOMENT_VIDEO_HEARTBEAT_PATH=str(heartbeat)), \
                    patch(self.command_module+"check_video_tools"), \
                    patch(self.command_module+"process_one",side_effect=process):
                with self.assertRaises(KeyboardInterrupt):
                    call_command("process_moment_videos",watch=True,stdout=io.StringIO())
            self.assertFalse(heartbeat.exists())

    def test_single_pass_handles_silent_small_video_and_hdr_portrait(self):
        ffmpeg=shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is required for the synthetic video check")
        cases=[("small","640x360",["-c:v","libx264"],False),
            ("hdr","720x1280",["-c:v","libx265","-pix_fmt","yuv420p10le",
                "-x265-params","pools=1:frame-threads=1:log-level=error",
                "-color_primaries","bt2020","-color_trc","smpte2084","-colorspace","bt2020nc"],True)]
        for name,size,codec,has_high in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);source=root/"source.mp4";output=root/"output";output.mkdir()
                subprocess.run([ffmpeg,"-v","error","-f","lavfi","-i",f"testsrc2=size={size}:rate=30",
                    "-t","1.2",*codec,str(source)],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                duration,dimensions,files=encode(source,output)
                self.assertEqual(duration,1200)
                self.assertEqual(dimensions,tuple(map(int,size.split("x"))))
                names={p.name for p in files}
                self.assertIn("poster.jpg",names)
                self.assertEqual("720.m3u8" in names,has_high)
                for playlist in output.glob("[37]*.m3u8"):
                    subprocess.run([ffmpeg,"-v","error","-i",str(playlist),"-f","null","-"],
                        check=True,timeout=15,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


class MomentVideoTests(APITestCase):
    setUp = MomentAPITests.setUp

    def start(self, data=b"synthetic"):
        draft, video = str(uuid4()), str(uuid4())
        result = self.client.post('/api/social/moment-drafts/', {'request_id':draft,'couple':self.couple.pk}, format='json')
        self.assertEqual(result.status_code, 201)
        base = f'/api/social/moment-drafts/{draft}/videos/{video}/'
        result = self.client.post(base, {'source_bytes':len(data)}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return draft, video, base

    def upload(self, base, data):
        for offset in range(0, len(data), CHUNK_BYTES):
            result = self.client.put(base+f'chunks/{offset}/',data[offset:offset+CHUNK_BYTES], content_type='application/octet-stream')
            self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post(base+'complete/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_resume_replay_conflict_and_owner_boundaries(self):
        data = b'a' * CHUNK_BYTES + b'b'
        draft, video, base = self.start(data)
        first = self.client.put(base+'chunks/0/', data[:CHUNK_BYTES], content_type='application/octet-stream')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.client.get(base).data['data']['received_bytes'], CHUNK_BYTES)
        self.assertEqual(self.client.put(base+'chunks/0/',data[:CHUNK_BYTES],content_type='application/octet-stream').status_code,200)
        self.assertEqual(self.client.put(base+'chunks/0/',b'x'*CHUNK_BYTES,content_type='application/octet-stream').status_code,409)
        self.assertEqual(self.client.post(base+'complete/',{},format='json').status_code,400)
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.get(base).status_code,404)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.put(base+f'chunks/{CHUNK_BYTES}/',b'b',content_type='application/octet-stream').status_code,404)
        self.client.force_authenticate(self.creator)
        self.upload(base,data)
        self.assertEqual(MomentVideo.objects.get(pk=video).state,'queued')
        body={'expected_revision':2,'photo_ids':[video]}
        self.assertEqual(self.client.post(f'/api/social/moment-drafts/{draft}/publish/',body,format='json').status_code,400)

    def test_status_polls_do_not_consume_draft_write_allowance(self):
        _, _, base = self.start()
        for _ in range(120):
            self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.get(base).status_code, 429)
        self.upload(base, b"synthetic")

    def test_limits_invalid_media_and_removal_cleanup(self):
        draft,video,base=self.start()
        self.assertEqual(self.client.post(base,{'source_bytes':MAX_BYTES+1},format='json').status_code,400)
        self.assertEqual(self.client.put(base+'chunks/0/',b'x'*(CHUNK_BYTES+1),content_type='application/octet-stream').status_code,413)
        self.upload(base,b'synthetic')
        process_one()
        row=MomentVideo.objects.get(pk=video)
        self.assertEqual(row.state,'failed')
        self.assertEqual(row.error_code,'invalid_video')
        self.assertEqual(self.client.post(base+'retry/',{},format='json').data['data']['state'],'failed')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'/api/social/moment-drafts/{draft}/photos/{video}/').status_code,200)
        self.assertFalse(MomentVideo.objects.filter(pk=video).exists())

        self.assertFalse(MomentVideoFile.objects.exists())
        self.assertFalse(list(Path(self.media.name).rglob("*.part")))

    def test_chunk_throttles_cover_replays_and_keep_resume_state(self):
        from django.core.cache import cache
        for throttle in ["VideoChunkBurstThrottle", "VideoChunkHourlyThrottle"]:
            cache.clear()
            _,video,base=self.start(b"x")
            with patch("social.api.moment_videos."+throttle+".rate","2/minute"):
                for _ in range(2):
                    self.assertEqual(self.client.put(base+"chunks/0/",b"x",content_type="application/octet-stream").status_code,200)
                self.assertEqual(self.client.put(base+"chunks/0/",b"x",content_type="application/octet-stream").status_code,429)
            self.assertEqual(MomentVideo.objects.get(pk=video).received_bytes,1)
            self.assertEqual(self.client.get(base).status_code,200)

    def test_owner_deletion_cascades_both_published_and_draft_video_references(self):
        from social.models import CoupleMomentPhotoModel
        _,video,_=self.start()
        moment=CoupleMomentModel.objects.create(couple=self.couple,created_by=self.creator,
            caption="Synthetic",moment_date=timezone.now().date())
        CoupleMomentPhotoModel.objects.create(moment=moment,video_id=video,image="synthetic-poster.jpg")
        with self.captureOnCommitCallbacks(execute=True):
            self.creator.delete()
        self.assertFalse(MomentVideo.objects.filter(pk=video).exists())
        self.assertFalse(CoupleMomentPhotoModel.objects.filter(moment_id=moment.pk).exists())

    def test_worker_failure_retry_and_stale_lease(self):
        _,video,base=self.start()
        self.upload(base,b'synthetic')
        with patch('social.services.moment_video_processing.encode',side_effect=OSError('synthetic')):
            process_one()
        self.assertEqual(MomentVideo.objects.get(pk=video).error_code,'processing')
        self.assertEqual(self.client.post(base+'retry/',{},format='json').data['data']['state'],'queued')
        MomentVideo.objects.filter(pk=video).update(state='processing', updated_at=timezone.now()-timedelta(minutes=11),attempts=3)
        process_one()
        self.assertEqual(MomentVideo.objects.get(pk=video).state,'failed')

    def test_real_encoding_publication_streaming_cookie_expiry_and_deletion(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('ffmpeg is required for the synthetic video integration check')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.mp4'
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=360x720:rate=30',
                '-f','lavfi','-i','sine=frequency=440','-t','1.2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
            data=path.read_bytes()
        draft,video,base=self.start(data)
        self.upload(base,data)
        self.assertTrue(process_one())
        row=MomentVideo.objects.get(pk=video)
        self.assertEqual(row.state,'ready', row.error_code)
        self.assertFalse(row.files.filter(name__startswith='upload_').exists())
        body={'expected_revision':2,'photo_ids':[video]}
        moment=self.client.post(f'/api/social/moment-drafts/{draft}/publish/',body,format='json')
        self.assertEqual(moment.status_code,201,moment.data)
        mid=moment.data['data']['id']
        self.assertEqual(moment.data['data']['photos'][0]['video']['state'],'ready')
        poster_response=self.client.get(moment.data['data']['photos'][0]['image'])
        self.assertEqual(poster_response.status_code,200)
        self.assertTrue(b"".join(poster_response.streaming_content).startswith(b"\xff\xd8"))
        self.assertEqual((row.width,row.height),(360,720))
        self.assertEqual(moment.data['data']['photos'][0]['height'],640)
        self.assertEqual(self.client.post(f'/api/social/moment-drafts/{draft}/publish/',body,format='json').status_code,200)
        self.client.force_authenticate(self.other)
        handshake=f'/api/social/moment-videos/{video}/playback/'
        self.assertEqual(self.client.post(handshake,{},format='json').status_code,404)
        self.client.force_authenticate(self.partner)
        access=self.client.post(handshake,{},format='json').data['data']
        stream=f'/api/social/moment-video-stream/{video}/'
        anonymous=APIClient()
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code,401)
        anonymous.cookies[COOKIE]=access['cookie_value']
        response=anonymous.get(stream+'master.m3u8')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'360.m3u8',b''.join(response.streaming_content))
        response=anonymous.get(stream+'720_000.ts',HTTP_RANGE='bytes=0-31')
        self.assertEqual(response.status_code,206)
        self.assertEqual(len(b''.join(response.streaming_content)),32)
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertEqual(anonymous.get(stream+'720_000.ts',HTTP_RANGE='bytes=99999999-').status_code,416)
        self.assertEqual(anonymous.get(stream+'upload_0000').status_code,404)
        head = anonymous.head(stream+'720_000.ts', HTTP_RANGE='bytes=-32')
        self.assertEqual(head.status_code, 206)
        self.assertEqual(head['Content-Length'], '32')
        session = DeviceSession.objects.create(user=self.partner, expires_at=timezone.now()+timedelta(days=1))
        payload = signing.loads(access['cookie_value'], salt=SALT)
        payload['sid'] = str(session.pk)
        anonymous.cookies[COOKIE] = signing.dumps(payload, salt=SALT)
        self.assertEqual(anonymous.head(stream+'master.m3u8').status_code, 200)
        session.revoked_at = timezone.now(); session.save()
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code, 401)
        anonymous.cookies[COOKIE] = access['cookie_value']
        membership = CoupleMembershipModel.objects.get(couple=self.couple, user=self.partner)
        membership.ended_at = timezone.now(); membership.save()
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code, 404)
        membership.ended_at = None; membership.save()
        block = SingleConnectionModel.objects.create(sender_number=self.partner.phone_number,
            receiver_number=self.creator.phone_number, connection_status='BLOCKED')
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code, 404)
        block.delete()
        payload=signing.loads(access['cookie_value'],salt=SALT);payload['exp']=1
        anonymous.cookies[COOKIE]=signing.dumps(payload,salt=SALT)
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code,401)
        anonymous.cookies[COOKIE]=access['cookie_value']
        CoupleMomentModel.objects.filter(pk=mid).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(anonymous.get(stream+'master.m3u8').status_code,404)
        with self.captureOnCommitCallbacks(execute=True):
            CoupleMomentModel.objects.filter(pk=mid).delete()
        self.assertFalse(MomentVideo.objects.filter(pk=video).exists())


class MomentVideoConcurrencyTests(APITransactionTestCase):
    setUp = MomentAPITests.setUp
    start = MomentVideoTests.start

    def test_duplicate_chunks_commit_once_and_cleanup_the_losing_file(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connection, close_old_connections
        from django.core.files.base import ContentFile
        from social.services.moment_video import upload_chunk
        if connection.vendor != 'postgresql':
            self.skipTest('Row-lock verification requires disposable PostgreSQL')
        data = b'synthetic chunk'
        draft, video, _ = self.start(data)
        barrier = Barrier(2)
        storage=MomentVideoFile._meta.get_field("file").storage
        save=storage.save
        def competing_save(*args,**kwargs):
            path=save(*args,**kwargs)
            barrier.wait(timeout=10)  # Both missed the first lookup and wrote distinct files.
            return path
        def send():
            close_old_connections()
            try:
                return upload_chunk(self.creator, draft, video, 0, ContentFile(data)).received_bytes
            finally:
                close_old_connections()
        with patch.object(storage,"save",side_effect=competing_save), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(results, [len(data), len(data)])
        self.assertEqual(MomentVideoFile.objects.filter(video_id=video).count(), 1)
        self.assertEqual(len(list(Path(self.media.name).rglob('*.part'))), 1)
