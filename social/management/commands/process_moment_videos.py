import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from social.services.moment_video_processing import process_one


class Command(BaseCommand):
    help = "Process bounded video Moment jobs, with stale-job recovery."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            if not process_one():
                if not options["watch"]:
                    break
                time.sleep(2)
