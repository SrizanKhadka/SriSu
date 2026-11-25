from django.core.management.base import BaseCommand
from django.db.models import Q

from chat.models import MatrixRoomMapping, MatrixUserMapping
from chat.services.matrix_provisioning import (
    ensure_matrix_mapping_records,
    mark_matrix_mapping_revoke_pending,
)
from social.models import CoupleModel, SingleConnectionModel
from social.services.relationship_service import (
    RelationshipConflict,
    reconcile_accepted_relationship,
)
from utils.choices import CoupleConnectionStatus, SingleConnectionStatus


class Command(BaseCommand):
    help = "Audit accepted relationships and optionally create/repair their one scoped room."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Commit safe missing-room and participant reconciliation.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        checked = repaired = invalid = 0
        couples = CoupleModel.objects.filter(
            couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
            couple_connection__ended_at__isnull=True,
        ).order_by("pk")
        for couple_id in couples.values_list("pk", flat=True).iterator():
            checked += 1
            couple = CoupleModel.objects.get(pk=couple_id)
            member_rows = list(
                couple.memberships.filter(ended_at__isnull=True).values_list(
                    "user_id", "user__phone_number"
                )
            )
            if len(member_rows) != 2:
                invalid += 1
                continue
            members = [row[0] for row in member_rows]
            first_phone, second_phone = [row[1] for row in member_rows]
            rooms = list(couple.chat_rooms.values_list("pk", "user_one_id", "user_two_id"))
            blocked = SingleConnectionModel.objects.filter(
                connection_status=SingleConnectionStatus.BLOCKED,
            ).filter(
                Q(sender_number=first_phone, receiver_number=second_phone)
                | Q(sender_number=second_phone, receiver_number=first_phone)
            ).exists()
            if blocked:
                revocable = list(
                    MatrixRoomMapping.objects.filter(
                        chat_room_id__in=[room[0] for room in rooms]
                    ).exclude(
                        state__in=[
                            MatrixRoomMapping.State.REVOKE_PENDING,
                            MatrixRoomMapping.State.REVOKING,
                            MatrixRoomMapping.State.REVOKED,
                        ]
                    )
                )
                if revocable:
                    repaired += 1
                    if apply:
                        for mapping in revocable:
                            mark_matrix_mapping_revoke_pending(mapping)
                # BLOCKED is authoritative: never create a new room or Matrix
                # mapping for this accepted relationship.
                continue
            room_needs_repair = len(rooms) != 1 or set(rooms[0][1:]) != set(members)
            matrix_needs_repair = True
            if not room_needs_repair:
                room_id = rooms[0][0]
                matrix_needs_repair = (
                    not MatrixRoomMapping.objects.filter(chat_room_id=room_id).exists()
                    or MatrixUserMapping.objects.filter(user_id__in=members).count() != 2
                )
            needs_repair = room_needs_repair or matrix_needs_repair
            if needs_repair and apply:
                try:
                    room = (
                        reconcile_accepted_relationship(couple_id)
                        if room_needs_repair
                        else couple.chat_rooms.get(pk=rooms[0][0])
                    )
                    # Database-only backfill. The separately supervised Matrix
                    # reconciler owns all remote provisioning and retry work.
                    ensure_matrix_mapping_records(room)
                except RelationshipConflict:
                    invalid += 1
                    continue
                repaired += 1
            elif needs_repair:
                repaired += 1
        mode = "applied" if apply else "dry-run"
        self.stdout.write(
            f"mode={mode} checked={checked} needs_repair={repaired} invalid={invalid}"
        )
