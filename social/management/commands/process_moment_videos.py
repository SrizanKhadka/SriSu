import time
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from social.services.moment_video_processing import process_one, check_video_tools


class Command(BaseCommand):
    help = "Process bounded video Moment jobs, with stale-job recovery."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--check", action="store_true", help="Check FFmpeg/ffprobe without claiming jobs.")
        parser.add_argument("--healthcheck", action="store_true", help="Check the supervised worker heartbeat.")

    def handle(self, *args, **options):
        heartbeat = Path(getattr(settings, "MOMENT_VIDEO_HEARTBEAT_PATH",
            Path(tempfile.gettempdir()) / "srisu-moment-video-worker.heartbeat"))
        if options["healthcheck"]:
            try:
                # One encode is bounded to 180s, plus probe and storage work.
                if not 0 <= time.time() - heartbeat.stat().st_mtime < 300:
                    raise OSError()
            except OSError:
                raise CommandError("Video worker heartbeat missing or stale. Start moment_video_worker and inspect its logs.")
            self.stdout.write("Video worker heartbeat is healthy.")
            return
        try:
            check_video_tools()
        except Exception:
            raise CommandError("Video processing needs FFmpeg with H.264/AAC encoders and ffprobe. Rebuild the backend image or install FFmpeg.")
        if options["check"]:
            self.stdout.write("Video processing tools are ready.")
            return
        self.stdout.write("Video worker ready; watching the queue." if options["watch"] else "Processing queued videos.")
        try:
            while True:
                close_old_connections()
                if options["watch"]:
                    heartbeat.touch()
                started = time.monotonic()
                handled = process_one()
                if handled:
                    # No account IDs, media paths, tokens or private content.
                    self.stdout.write(f"Video job handled in {time.monotonic()-started:.2f}s.")
                if not handled:
                    if not options["watch"]:
                        break
                    time.sleep(2)
        finally:
            if options["watch"]:
                heartbeat.unlink(missing_ok=True)
