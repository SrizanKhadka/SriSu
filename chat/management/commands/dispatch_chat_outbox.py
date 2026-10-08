import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError

from chat.services.outbox import dispatch_pending


class Command(BaseCommand):
    help = "Publish one bounded batch of committed chat-v2 change hints."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Continuously publish bounded batches until SIGINT or SIGTERM.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to wait between watched batches (default: 1).",
        )

    def handle(self, *args, **options):
        if options["poll_interval"] <= 0:
            raise CommandError("poll-interval must be greater than zero")
        if not options["watch"]:
            self._dispatch(options["limit"], always_report=True)
            return

        stopped = Event()
        previous_handlers = {}

        def request_stop(signum, frame):
            stopped.set()

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, request_stop)
            self.stdout.write("chat_outbox_worker=started")
            while not stopped.is_set():
                self._dispatch(options["limit"], always_report=False)
                if stopped.wait(options["poll_interval"]):
                    break
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            self.stdout.write("chat_outbox_worker=stopped")

    def _dispatch(self, limit, *, always_report):
        result = dispatch_pending(batch_size=limit)
        if not always_report and not result["claimed"] and not result["failed"]:
            return
        self.stdout.write(
            f"claimed={result['claimed']} published={result['published']} failed={result['failed']}"
        )
