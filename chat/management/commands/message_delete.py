from django.core.management.base import BaseCommand
from chat.models import MessageModel, ChatRoom
from authentication.models import UserModel

class Command(BaseCommand):
    help = "Delete bulk seeded messages for load testing cleanup"

    def handle(self, *args, **options):
        chat_room_id = "7fe512b9-548b-4a21-93cd-0a25d1aed5b4"
        sender_id = 95
        receiver_id = 97
        couple_id = 2

        chat_room = ChatRoom.objects.get(id=chat_room_id)
        sender = UserModel.objects.get(id=sender_id)
        receiver = UserModel.objects.get(id=receiver_id)

        self.stdout.write(self.style.WARNING("🧹 Deleting seeded messages..."))

        queryset = MessageModel.objects.filter(
            chat_room=chat_room,
            couple_id=couple_id,
            sender__in=[sender, receiver],
            receiver__in=[sender, receiver],
        )

        total = queryset.count()

        if total == 0:
            self.stdout.write(self.style.WARNING("⚠️ No messages found to delete"))
            return

        # ⚡ Fast bulk delete (single SQL query)
        queryset.delete()

        self.stdout.write(
            self.style.SUCCESS(f"✅ Successfully deleted {total} messages")
        )
