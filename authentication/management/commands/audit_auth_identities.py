"""Read-only preflight. Never print identities or auto-rename users."""
import re
from django.core.management.base import BaseCommand
from django.db.models import Count
from authentication.models import UserModel

class Command(BaseCommand):
    help = "Report counts of identity conflicts before authentication migration."

    def handle(self, *args, **options):
        duplicates = UserModel.objects.exclude(username__isnull=True).exclude(username="").values("username").annotate(n=Count("id")).filter(n__gt=1).count()
        noncanonical = sum(not bool(re.fullmatch(r"\+[1-9][0-9]{7,13}", value)) for value in UserModel.objects.values_list("phone_number", flat=True).iterator())
        self.stdout.write(f"Duplicate exact username groups: {duplicates}; noncanonical phone records: {noncanonical}.")
        if duplicates or noncanonical:
            self.stdout.write("Resolve conflicts with affected users before rollout. This command changed no data.")
