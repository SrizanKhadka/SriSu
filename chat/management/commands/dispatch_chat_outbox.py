from django.core.management.base import BaseCommand

from chat.services.outbox import dispatch_pending


class Command(BaseCommand):
    help = "Publish one bounded batch of committed chat-v2 change hints."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        result = dispatch_pending(batch_size=options["limit"])
        self.stdout.write(
            f"claimed={result['claimed']} published={result['published']} failed={result['failed']}"
        )
