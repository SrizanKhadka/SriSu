"""Bounded, resumable legacy-message sequencing for chat v2."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from chat.models import ChatChange, ChatRoom, MessageModel


class Command(BaseCommand):
    help = "Assign stable room sequences to legacy messages in bounded atomic batches."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--max-batches", type=int, default=10)
        parser.add_argument("--check", action="store_true")

    @staticmethod
    def _assert_safe_mixed_state(room):
        oldest_unsequenced = (
            MessageModel.objects.filter(chat_room=room, sequence__isnull=True)
            .order_by("timestamp", "id")
            .values_list("timestamp", "id")
            .first()
        )
        newest_sequenced = (
            MessageModel.objects.filter(chat_room=room, sequence__isnull=False)
            .order_by("-timestamp", "-id")
            .values_list("timestamp", "id")
            .first()
        )
        if (
            oldest_unsequenced is not None
            and newest_sequenced is not None
            and newest_sequenced > oldest_unsequenced
        ):
            raise CommandError(
                f"room {room.pk} has unsafe mixed sequence chronology; "
                "stop writers and reconcile it explicitly"
            )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        max_batches = options["max_batches"]
        if not 1 <= batch_size <= 5000 or not 1 <= max_batches <= 1000:
            raise CommandError("batch-size must be 1..5000 and max-batches 1..1000")
        remaining = MessageModel.objects.filter(sequence__isnull=True).count()
        if options["check"]:
            for room_id in (
                MessageModel.objects.filter(sequence__isnull=True)
                .order_by()
                .values_list("chat_room_id", flat=True)
                .distinct()
                .iterator(chunk_size=500)
            ):
                self._assert_safe_mixed_state(ChatRoom.objects.get(pk=room_id))
            self.stdout.write(f"remaining={remaining}")
            return

        processed = batches = 0
        while batches < max_batches:
            room_id = (
                MessageModel.objects.filter(sequence__isnull=True)
                .order_by("chat_room_id")
                .values_list("chat_room_id", flat=True)
                .first()
            )
            if room_id is None:
                break
            with transaction.atomic():
                room = ChatRoom.objects.select_for_update().get(pk=room_id)
                self._assert_safe_mixed_state(room)
                messages = list(
                    MessageModel.objects.select_for_update()
                    .filter(chat_room=room, sequence__isnull=True)
                    .order_by("timestamp", "id")[:batch_size]
                )
                high = max(
                    room.last_sequence,
                    ChatChange.objects.filter(chat_room=room).aggregate(value=Max("sequence"))["value"] or 0,
                )
                changes = []
                for message in messages:
                    high += 1
                    message.sequence = high
                    message.legacy_plaintext = True
                    message.content_kind = "legacy"
                    changes.append(ChatChange(
                        chat_room=room,
                        sequence=high,
                        kind="legacy_message_created",
                        message=message,
                        message_revision=message.revision,
                        actor_id=message.sender_id,
                        metadata={"backfilled": True},
                    ))
                MessageModel.objects.bulk_update(
                    messages,
                    ["sequence", "legacy_plaintext", "content_kind"],
                )
                ChatChange.objects.bulk_create(changes)
                room.last_sequence = high
                room.save(update_fields=["last_sequence"])
            processed += len(messages)
            batches += 1
        remaining = MessageModel.objects.filter(sequence__isnull=True).count()
        self.stdout.write(
            f"processed={processed} batches={batches} remaining={remaining}"
        )
