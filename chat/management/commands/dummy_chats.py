import random
from faker import Faker
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from authentication.models import UserModel
from chat.models import MessageModel, ChatRoom
from utils.choices import MessageType

class Command(BaseCommand):
    help = "Seed bulk messages for load & performance testing"

    def __init__(self):
        super().__init__()
        self.fake = Faker()

    def handle(self, *args, **options):
        TOTAL_MESSAGES = 50   # change to 50_000 if needed
        BATCH_SIZE = 1_000        # bulk_create chunk size

        chat_room_id = "7fe512b9-548b-4a21-93cd-0a25d1aed5b4"
        sender_id = 97
        receiver_id = 95
        couple_id = 2

        sender = UserModel.objects.get(id=sender_id)
        receiver = UserModel.objects.get(id=receiver_id)
        chat_room = ChatRoom.objects.get(id=chat_room_id)

        messages = []
        start_time = timezone.now() - timedelta(days=2)

        self.stdout.write(self.style.WARNING(" Seeding messages..."))

        for i in range(TOTAL_MESSAGES):
            # Alternate sender for realism
            if i % 2 == 0:
                from_user = sender
                to_user = receiver
            else:
                from_user = receiver
                to_user = sender

            message = MessageModel(
                chat_room=chat_room,
                sender=from_user,
                receiver=to_user,
                couple_id=couple_id,
                message_type=MessageType.TEXT,
                text=self.fake.sentence(nb_words=random.randint(3, 12)),
                is_delivered=True,
                is_read=random.choice([True, False]),
                timestamp=start_time + timedelta(seconds=i * random.randint(1, 3)),
            )

            messages.append(message)

            # Bulk insert in chunks (VERY IMPORTANT)
            if len(messages) >= BATCH_SIZE:
                MessageModel.objects.bulk_create(messages)
                messages.clear()

                self.stdout.write(
                    self.style.SUCCESS(f"Inserted {i + 1} messages")
                )

        # Insert remaining
        if messages:
            MessageModel.objects.bulk_create(messages)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {TOTAL_MESSAGES} messages in chat room {chat_room_id}"
            )
        )
