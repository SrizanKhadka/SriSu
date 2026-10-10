from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from couple_chat.models import Attachment
from couple_chat.media import cleanup_files, cleanup_ids


class Command(BaseCommand):
    help="Remove expired unfinished ciphertext, deleted attachments and queued storage files."

    def add_arguments(self,parser):
        parser.add_argument("--dry-run",action="store_true")

    def handle(self,*args,**options):
        now=timezone.now()
        with transaction.atomic():
            expired=Attachment.objects.filter(deleted_at__isnull=True).filter(
                Q(message__isnull=True,expires_at__lte=now)|Q(room__revoked_at__isnull=False))
            ids=list(expired.select_for_update().order_by("pk").values_list("pk",flat=True)[:100])
            if not options["dry_run"]: Attachment.objects.filter(pk__in=ids).update(deleted_at=now)
        if not options["dry_run"]:
            pending=list(Attachment.objects.filter(deleted_at__isnull=False).exclude(blob="").values_list("pk",flat=True)[:100])
            cleanup_ids(pending); cleanup_files()
        self.stdout.write(f"{'would_expire' if options['dry_run'] else 'expired'}={len(ids)}")
