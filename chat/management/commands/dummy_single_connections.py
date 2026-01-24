import random
from django.core.management.base import BaseCommand
from django.db import transaction

from authentication.models import UserModel
from chat.models import SingleConnectionModel, SingleConnectionStatus, GenderChoices


class Command(BaseCommand):
    help = "Seed single connections for a female user to all male users"

    def handle(self, *args, **options):
        SENDER_PHONE = "+9779865453512"

        try:
            sender = UserModel.objects.get(phone_number=SENDER_PHONE)
        except UserModel.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Sender {SENDER_PHONE} not found"))
            return

        # Get all male users except the sender
        male_users = UserModel.objects.filter(gender=GenderChoices.MALE).exclude(id=sender.id)
        total_males = male_users.count()
        self.stdout.write(self.style.WARNING(f"Found {total_males} male users"))

        connections = []
        for male in male_users:
            # Random status for realism
            status = SingleConnectionStatus.ACCEPTED

            connection = SingleConnectionModel(
                sender_number=sender.phone_number,
                receiver_number=male.phone_number,
                connection_status=status
            )
            connections.append(connection)

        BATCH_SIZE = 500  # safe chunk size

        # Bulk insert in chunks
        for i in range(0, len(connections), BATCH_SIZE):
            batch = connections[i:i+BATCH_SIZE]
            with transaction.atomic():
                SingleConnectionModel.objects.bulk_create(batch)
            self.stdout.write(self.style.SUCCESS(f"Inserted {i + len(batch)} / {len(connections)} connections"))

        self.stdout.write(self.style.SUCCESS(
            f"Successfully created {len(connections)} single connections for sender {SENDER_PHONE}"
        ))
