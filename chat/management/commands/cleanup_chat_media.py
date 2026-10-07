from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from chat.models import ChatOutbox, EncryptedAttachment, MediaModel, MessageModel


class Command(BaseCommand):
    help = "Delete a bounded batch of abandoned private chat-media blobs and rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=settings.CHAT_MEDIA_CLEANUP_BATCH_SIZE,
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = timezone.now()
        cutoff = now - timedelta(seconds=settings.CHAT_LEGACY_MEDIA_TTL_SECONDS)
        deleted = failed = pruned_outbox = 0

        legacy_ids = list(
            MediaModel.objects.filter(messages__isnull=True)
            .filter(
                Q(claimed_at__isnull=True, uploaded_at__lt=cutoff)
                | Q(claimed_at__lt=cutoff)
            )
            .order_by("id")
            .values_list("id", flat=True)[:limit]
        )
        for row_id in legacy_ids:
            with transaction.atomic():
                row = MediaModel.objects.select_for_update().filter(pk=row_id).first()
                if row is None or row.messages.exists():
                    continue
                eligible = (
                    row.claimed_at is None and row.uploaded_at < cutoff
                ) or (
                    row.claimed_at is not None and row.claimed_at < cutoff
                )
                if not eligible:
                    continue
                try:
                    row.file.delete(save=False)
                except Exception:
                    failed += 1
                    continue
                row.delete()
                deleted += 1

        remaining = limit - len(legacy_ids)
        if remaining > 0:
            tombstoned_ids = list(
                MessageModel.objects.filter(is_deleted=True)
                .exclude(media__isnull=True)
                .exclude(media="")
                .order_by("id")
                .values_list("id", flat=True)[:remaining]
            )
            for message_id in tombstoned_ids:
                with transaction.atomic():
                    message = MessageModel.objects.select_for_update().filter(
                        pk=message_id,
                        is_deleted=True,
                    ).exclude(media__isnull=True).exclude(media="").first()
                    if message is None:
                        continue
                    name = message.media.name
                    try:
                        message.media.storage.delete(name)
                    except Exception:
                        failed += 1
                        continue
                    message.media = ""
                    message.save(update_fields=["media"])
                    deleted += 1
            remaining -= len(tombstoned_ids)

        if remaining > 0:
            attachment_ids = list(
                EncryptedAttachment.objects.filter(
                    Q(
                        claimed_message__isnull=True,
                        state=EncryptedAttachment.State.CANCELLED,
                    )
                    | Q(
                        claimed_message__isnull=True,
                        state=EncryptedAttachment.State.STAGED,
                        expires_at__lte=now,
                    )
                    | Q(
                        state=EncryptedAttachment.State.CLAIMED,
                        claimed_message__is_deleted=True,
                    )
                    | Q(
                        state=EncryptedAttachment.State.CLAIMED,
                        claimed_message__tombstoned_at__isnull=False,
                    )
                )
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:remaining]
            )
            for row_id in attachment_ids:
                with transaction.atomic():
                    row = EncryptedAttachment.objects.select_for_update().filter(
                        pk=row_id
                    ).first()
                    if row is None:
                        continue
                    eligible = (
                        row.claimed_message_id is None
                        and row.state == EncryptedAttachment.State.CANCELLED
                    ) or (
                        row.claimed_message_id is None
                        and row.state == EncryptedAttachment.State.STAGED
                        and row.expires_at <= now
                    ) or (
                        row.state == EncryptedAttachment.State.CLAIMED
                        and row.claimed_message_id is not None
                        and MessageModel.objects.filter(
                            pk=row.claimed_message_id,
                        ).filter(
                            Q(is_deleted=True) | Q(tombstoned_at__isnull=False)
                        ).exists()
                    )
                    if not eligible:
                        continue
                    try:
                        row.ciphertext.delete(save=False)
                    except Exception:
                        failed += 1
                        continue
                    row.delete()
                    deleted += 1

        # Published rows are only delivery bookkeeping. ChatChange is the
        # durable catch-up log and is deliberately retained.
        outbox_cutoff = now - timedelta(
            seconds=settings.CHAT_V2_OUTBOX_RETENTION_SECONDS
        )
        published_ids = list(
            ChatOutbox.objects.filter(
                state=ChatOutbox.State.PUBLISHED,
                published_at__lt=outbox_cutoff,
            )
            .order_by("published_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        if published_ids:
            pruned_outbox, _ = ChatOutbox.objects.filter(
                id__in=published_ids
            ).delete()

        self.stdout.write(
            f"deleted={deleted} failed={failed} pruned_outbox={pruned_outbox}"
        )
