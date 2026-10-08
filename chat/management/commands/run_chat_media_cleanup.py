"""Supervise bounded chat-media cleanup for local/development Compose.

Production should schedule the one-shot ``cleanup_chat_media`` command with its
normal process supervisor instead of depending on this development convenience.
"""

import signal
from threading import Event

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run bounded chat-media cleanup periodically until SIGINT or SIGTERM."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=300.0,
            help="Seconds to wait between cleanup batches (default: 300).",
        )

    def handle(self, *args, **options):
        if options["poll_interval"] <= 0:
            raise CommandError("poll-interval must be greater than zero")

        stopped = Event()
        previous_handlers = {}

        def request_stop(signum, frame):
            stopped.set()

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, request_stop)
            self.stdout.write("chat_media_cleanup_worker=started")
            while not stopped.is_set():
                command_options = {"stdout": self.stdout, "stderr": self.stderr}
                if options["limit"] is not None:
                    command_options["limit"] = options["limit"]
                call_command("cleanup_chat_media", **command_options)
                if stopped.wait(options["poll_interval"]):
                    break
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            self.stdout.write("chat_media_cleanup_worker=stopped")
