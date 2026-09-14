from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from social.models import CoupleMomentModel, CoupleMomentPhotoModel, MomentFileDeletion
from social.services.moment_service import delete_file


class Command(BaseCommand):
    help = "Purge moments 30 days after expiry and retry queued file deletions."

    def add_arguments(self, parser):
        parser.add_argument("--sweep-orphans", action="store_true",
            help="Also remove unreferenced files older than 24 hours under couples/moments/.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        # Bounded transactions avoid locking the entire retained history.
        count = 0
        while True:
            with transaction.atomic():
                ids = list(CoupleMomentModel.objects.select_for_update().filter(
                    expires_at__lte=cutoff).values_list("id", flat=True)[:100])
                if not ids:
                    break
                CoupleMomentModel.objects.filter(pk__in=ids).delete()
                count += len(ids)
        for name in MomentFileDeletion.objects.values_list("name", flat=True).iterator():
            delete_file(name)
        if options["sweep_orphans"]:
            storage = CoupleMomentPhotoModel._meta.get_field("image").storage
            grace = timezone.now() - timedelta(hours=24)

            def sweep(prefix):
                try:
                    directories, files = storage.listdir(prefix)
                except FileNotFoundError:
                    return
                for filename in files:
                    name = f"{prefix}/{filename}"
                    if storage.get_modified_time(name) < grace and not CoupleMomentPhotoModel.objects.filter(image=name).exists():
                        from social.services.moment_service import queue_file_deletion
                        queue_file_deletion(name)
                for directory in directories:
                    sweep(f"{prefix}/{directory}")

            sweep("couples/moments")
        self.stdout.write(f"Purged {count} moments; {MomentFileDeletion.objects.count()} file deletions pending.")
