from django.core.management.base import BaseCommand
from couple_chat.models import Change
from couple_chat.services import dispatch


class Command(BaseCommand):
    help = "Retry a bounded batch of committed couple-chat invalidations."

    def handle(self, *args, **options):
        ids = list(Change.objects.filter(dispatched_at__isnull=True).order_by("id").values_list("id", flat=True)[:100])
        sent = sum(dispatch(change_id) for change_id in ids)
        self.stdout.write(f"dispatched={sent} pending_in_batch={len(ids) - sent}")
