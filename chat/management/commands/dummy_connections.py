from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = "Retired dating seed command (does not modify data)."
    def handle(self, *args, **options):
        raise CommandError("Dating seeds are retired. Use the isolated couple test fixtures.")
