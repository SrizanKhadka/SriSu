import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from chat.models import MatrixRoomMapping
from chat.selectors.access import eligible_relationship_rooms
from chat.services.matrix_provisioning import (
    mark_matrix_mapping_revoke_pending,
    process_pending_remote_revocation,
    provision_matrix_room,
    revoke_matrix_room,
    verify_active_matrix_room,
)


class Command(BaseCommand):
    help = "Retry a bounded batch of pending private-Matrix room operations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=5.0)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit <= 0 or limit > 500:
            raise CommandError("--limit must be between 1 and 500")
        poll_interval = options["poll_interval"]
        if poll_interval <= 0 or poll_interval > 300:
            raise CommandError("--poll-interval must be greater than 0 and at most 300 seconds")
        while True:
            result = self._reconcile(limit)
            if result["processed"] or not options["watch"]:
                self.stdout.write(
                    f"processed={result['processed']} "
                    f"provisioned={result['provisioned']} "
                    f"revoked={result['revoked']} verified={result['verified']} "
                    f"cleaned={result['cleaned']} "
                    f"deferred={result['deferred']}"
                )
            if not options["watch"]:
                return
            try:
                time.sleep(poll_interval)
            except KeyboardInterrupt:
                return

    def _reconcile(self, limit):
        now = timezone.now()
        # Re-evaluate authorization on every pass. This turns relationship
        # revocation, account deactivation, and either-direction BLOCKED edges
        # into durable remote leave work rather than relying on the next HTTP
        # request to notice them.
        ineligible = MatrixRoomMapping.objects.exclude(
            chat_room_id__in=eligible_relationship_rooms().values("pk")
        ).exclude(
            state__in=[
                MatrixRoomMapping.State.REVOKE_PENDING,
                MatrixRoomMapping.State.REVOKING,
                MatrixRoomMapping.State.REVOKED,
            ]
        )
        ineligible.exclude(state=MatrixRoomMapping.State.PROVISIONING).update(
            state=MatrixRoomMapping.State.REVOKE_PENDING,
            available_at=now,
            locked_at=None,
            last_error_code="",
            updated_at=now,
        )
        for mapping in ineligible.filter(
            state=MatrixRoomMapping.State.PROVISIONING,
        ).order_by("id")[:limit]:
            mark_matrix_mapping_revoke_pending(mapping)
        cleanup_ids = list(
            MatrixRoomMapping.objects.exclude(pending_remote_revocations=[])
            .order_by("id")
            .values_list("id", flat=True)[:limit]
        )
        cleaned = 0
        deferred = 0
        for mapping_id in cleanup_ids:
            if process_pending_remote_revocation(mapping_id):
                cleaned += 1
            else:
                deferred += 1

        verify_interval = settings.MATRIX_REMOTE_VERIFY_INTERVAL_SECONDS
        if verify_interval < 30 or verify_interval > 3600:
            raise CommandError(
                "MATRIX_REMOTE_VERIFY_INTERVAL_SECONDS must be between 30 and 3600"
            )
        verify_before = now - timedelta(seconds=verify_interval)
        active_ids = list(
            MatrixRoomMapping.objects.filter(
                state=MatrixRoomMapping.State.ACTIVE,
                chat_room_id__in=eligible_relationship_rooms().values("pk"),
            )
            .filter(Q(remote_verified_at__isnull=True) | Q(remote_verified_at__lte=verify_before))
            .order_by("remote_verified_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        verified = 0
        for mapping_id in active_ids:
            if verify_active_matrix_room(mapping_id, recover=True):
                verified += 1
            else:
                deferred += 1

        now = timezone.now()
        stale_before = now - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
        # Cleanup has an independently bounded phase. Backed-off cleanup rows
        # must not consume the normal provisioning/verification budget and
        # starve current relationships indefinitely.
        remaining = max(0, limit - len(active_ids))
        rows = list(
            MatrixRoomMapping.objects.filter(available_at__lte=now)
            .filter(
                Q(state__in=[
                    MatrixRoomMapping.State.PENDING,
                    MatrixRoomMapping.State.FAILED,
                ])
                | Q(
                    state=MatrixRoomMapping.State.REVOKED,
                    chat_room_id__in=eligible_relationship_rooms().values("pk"),
                )
                | Q(state=MatrixRoomMapping.State.PROVISIONING, locked_at__isnull=True)
                | Q(state=MatrixRoomMapping.State.PROVISIONING, locked_at__lte=stale_before)
                | Q(state=MatrixRoomMapping.State.REVOKE_PENDING)
                | Q(state=MatrixRoomMapping.State.REVOKING, locked_at__isnull=True)
                | Q(state=MatrixRoomMapping.State.REVOKING, locked_at__lte=stale_before)
            )
            .order_by("available_at", "id")
            .values_list("id", "state")[:remaining]
        )
        provisioned = 0
        revoked = 0
        for mapping_id, state in rows:
            if state in {
                MatrixRoomMapping.State.REVOKE_PENDING,
                MatrixRoomMapping.State.REVOKING,
            }:
                if revoke_matrix_room(mapping_id):
                    revoked += 1
                else:
                    deferred += 1
            elif provision_matrix_room(mapping_id):
                provisioned += 1
            else:
                deferred += 1
        return {
            "processed": len(cleanup_ids) + len(active_ids) + len(rows),
            "provisioned": provisioned,
            "revoked": revoked,
            "verified": verified,
            "cleaned": cleaned,
            "deferred": deferred,
        }
