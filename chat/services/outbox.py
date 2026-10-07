"""Durable chat change publication; Channels is a recoverable wake-up hint."""

from __future__ import annotations

from datetime import timedelta
import random

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from chat.models import ChatOutbox


LOCK_TIMEOUT = timedelta(minutes=5)
MAX_BACKOFF_SECONDS = 300


def _claim(*, now, batch_size):
    with transaction.atomic():
        ChatOutbox.objects.filter(
            state=ChatOutbox.State.PUBLISHING,
            locked_at__lt=now - LOCK_TIMEOUT,
        ).update(
            state=ChatOutbox.State.PENDING,
            locked_at=None,
            available_at=now,
            last_error_code="stale_claim",
        )
        queryset = ChatOutbox.objects.filter(
            state=ChatOutbox.State.PENDING,
            available_at__lte=now,
        ).order_by("id")
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        rows = list(queryset[:batch_size])
        for row in rows:
            row.state = ChatOutbox.State.PUBLISHING
            row.locked_at = now
            row.attempts += 1
        if rows:
            ChatOutbox.objects.bulk_update(rows, ["state", "locked_at", "attempts"])
        return [row.pk for row in rows]


def _hint(row):
    change = row.change
    return {
        "type": "event",
        "action": "chat_v2_changed",
        "protocol_version": 2,
        "event_id": f"{change.chat_room_id}:{change.sequence}",
        "emitted_at": change.created_at.isoformat(),
        "room_id": str(change.chat_room_id),
        "data": {
            "chat_room_id": str(change.chat_room_id),
            "high_watermark": change.sequence,
        },
    }


def dispatch_pending(*, channel_layer=None, now_fn=timezone.now, jitter_fn=random.uniform, batch_size=None):
    """Publish one bounded batch and return counters for jobs/health checks."""
    now = now_fn()
    batch_size = batch_size or settings.CHAT_V2_OUTBOX_BATCH_SIZE
    ids = _claim(now=now, batch_size=batch_size)
    layer = channel_layer or get_channel_layer()
    published = failed = 0
    for row in ChatOutbox.objects.filter(pk__in=ids).select_related("change").order_by("id"):
        change = row.change
        group = (
            f"chat_user_{change.audience_user_id}"
            if change.audience_user_id
            else f"chat_room_{change.chat_room_id}"
        )
        try:
            async_to_sync(layer.group_send)(
                group,
                {
                    "type": "chat.broadcast",
                    "room_id": str(change.chat_room_id),
                    "payload": _hint(row),
                },
            )
        except Exception:
            base = min(MAX_BACKOFF_SECONDS, 2 ** min(row.attempts, 8))
            delay = base + jitter_fn(0, max(1.0, base * 0.25))
            ChatOutbox.objects.filter(pk=row.pk, state=ChatOutbox.State.PUBLISHING).update(
                state=ChatOutbox.State.PENDING,
                locked_at=None,
                available_at=now_fn() + timedelta(seconds=delay),
                last_error_code="channel_unavailable",
            )
            failed += 1
        else:
            ChatOutbox.objects.filter(pk=row.pk, state=ChatOutbox.State.PUBLISHING).update(
                state=ChatOutbox.State.PUBLISHED,
                locked_at=None,
                published_at=now_fn(),
                last_error_code="",
            )
            published += 1
    return {"claimed": len(ids), "published": published, "failed": failed}
