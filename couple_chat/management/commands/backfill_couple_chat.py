from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from social.models import CoupleConnectionModel
from social.services.moment_service import active_couples
from couple_chat.services import ensure_room


class Command(BaseCommand):
    help = "Create missing rooms for accepted couples; --dry-run rolls every write back."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        created = 0
        failures = 0
        for couple in active_couples().filter(private_chat__isnull=True).iterator(chunk_size=100):
            try:
                with transaction.atomic():
                    CoupleConnectionModel.objects.select_for_update().get(pk=couple.couple_connection_id)
                    ensure_room(couple)
                    created += 1
                    if options["dry_run"]:
                        transaction.set_rollback(True)
            except ValueError:
                failures += 1
        self.stdout.write(f"rooms={created} dry_run={options['dry_run']} conflicts={failures}")
        if failures:
            raise CommandError("Some relationships require manual review; no participant identities were logged.")
