"""Supervised bounded polling worker. No secrets or recipient metadata in output."""
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from notifications.worker import cleanup, run_once


class Command(BaseCommand):
    help = "Dispatch durable notification deliveries (use --once for a bounded batch)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch", type=int, default=100)

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            cleanup()
            Path("/tmp/srisu-notification-worker.heartbeat").touch()
            count = 0
            for _ in range(max(1, min(options["batch"], 1000))):
                if not run_once():
                    break
                count += 1
                Path("/tmp/srisu-notification-worker.heartbeat").touch()
            if options["once"]:
                self.stdout.write(f"Processed {count} deliveries.")
                return
            time.sleep(1 if count else 5)
