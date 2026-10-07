from django.core.management.base import BaseCommand

from social.models import CoupleModel
from social.services.relationship_service import (
    RelationshipConflict,
    reconcile_accepted_relationship,
)
from utils.choices import CoupleConnectionStatus


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
            members = list(
                couple.memberships.filter(ended_at__isnull=True).values_list("user_id", flat=True)
            )
            if len(members) != 2:
                invalid += 1
                continue
            rooms = list(couple.chat_rooms.values_list("pk", "user_one_id", "user_two_id"))
            needs_repair = len(rooms) != 1 or set(rooms[0][1:]) != set(members)
            if needs_repair and apply:
                try:
                    reconcile_accepted_relationship(couple_id)
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
