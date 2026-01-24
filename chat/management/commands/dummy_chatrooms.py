from django.core.management.base import BaseCommand
from django.db import transaction

from authentication.models import UserModel
from chat.models import ChatRoom, SingleConnectionModel
import uuid


class Command(BaseCommand):
    help = "Create chat rooms for all single connections of the female sender"

    def handle(self, *args, **options):
        SENDER_PHONE = "+9779865453512" 

        try:
            sender = UserModel.objects.get(phone_number=SENDER_PHONE)
        except UserModel.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Sender {SENDER_PHONE} not found"))
            return

        # Get all single connections where sender is the female
        single_connections = SingleConnectionModel.objects.filter(sender_number=SENDER_PHONE)
        total_connections = single_connections.count()
        self.stdout.write(self.style.WARNING(f"Found {total_connections} single connections"))

        chatrooms = []
        for sc in single_connections:
            try:
                receiver = UserModel.objects.get(phone_number=sc.receiver_number)
            except UserModel.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"Receiver {sc.receiver_number} not found, skipping"))
                continue

            # Ensure unique user_one / user_two ordering for constraint
            user_one, user_two = (sender, receiver) if sender.id < receiver.id else (receiver, sender)

            chatroom = ChatRoom(
                id=uuid.uuid4(),
                user_one=user_one,
                user_two=user_two,
                chat_type="single",
                couple=None,
                singles=sc
            )
            chatrooms.append(chatroom)

        BATCH_SIZE = 500
        for i in range(0, len(chatrooms), BATCH_SIZE):
            batch = chatrooms[i:i+BATCH_SIZE]
            with transaction.atomic():
                ChatRoom.objects.bulk_create(batch)
            self.stdout.write(self.style.SUCCESS(f"Inserted {i + len(batch)} / {len(chatrooms)} chat rooms"))

        self.stdout.write(self.style.SUCCESS(
            f"Successfully created {len(chatrooms)} chat rooms for single connections of {SENDER_PHONE}"
        ))
