"""Create deterministic synthetic accounts for local end-to-end testing."""

from __future__ import annotations

from datetime import date
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from authentication.models import UserInterestModel, UserModel
from authentication.otp import mock_code
from chat.models import MatrixRoomMapping
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from social.services.relationship_service import (
    RelationshipConflict,
    RelationshipPermissionDenied,
    accept_connection,
    create_connection_request,
)
from utils.choices import CoupleConnectionStatus


USERS = (
    {
        "key": "asha",
        "phone_number": "+9779800000101",
        "username": "asha_local_01",
        "full_name": "Asha Thapa",
        "gender": "FEMALE",
        "zodiac_sign": "VIRGO",
        "mood": "CALM",
        "dob": date(1997, 9, 8),
        "city": "Kathmandu",
        "country": "Nepal",
        "bio": "Product designer, coffee explorer, and weekend trail walker.",
        "interests": ("Coffee walks", "Himalayan travel", "Indie music"),
    },
    {
        "key": "bikash",
        "phone_number": "+9779800000102",
        "username": "bikash_local_02",
        "full_name": "Bikash Shrestha",
        "gender": "MALE",
        "zodiac_sign": "TAURUS",
        "mood": "ADVENTUROUS",
        "dob": date(1996, 5, 2),
        "city": "Kathmandu",
        "country": "Nepal",
        "bio": "Software engineer who plans road trips around good food.",
        "interests": ("Road trips", "Momo", "Photography"),
    },
    {
        "key": "mira",
        "phone_number": "+9779800000103",
        "username": "mira_local_03",
        "full_name": "Mira Gurung",
        "gender": "FEMALE",
        "zodiac_sign": "PISCES",
        "mood": "THOUGHTFUL",
        "dob": date(1998, 3, 4),
        "city": "Pokhara",
        "country": "Nepal",
        "bio": "Teacher, reader, and collector of quiet lakeside mornings.",
        "interests": ("Books", "Lakeside walks", "Acoustic music"),
    },
    {
        "key": "rohan",
        "phone_number": "+9779800000104",
        "username": "rohan_local_04",
        "full_name": "Rohan Rai",
        "gender": "MALE",
        "zodiac_sign": "LEO",
        "mood": "PLAYFUL",
        "dob": date(1997, 8, 11),
        "city": "Pokhara",
        "country": "Nepal",
        "bio": "Outdoor guide, home cook, and enthusiastic badminton partner.",
        "interests": ("Badminton", "Cooking", "Camping"),
    },
    {
        "key": "nisha",
        "phone_number": "+9779800000105",
        "username": "nisha_local_05",
        "full_name": "Nisha Karki",
        "gender": "FEMALE",
        "zodiac_sign": "AQUARIUS",
        "mood": "EXCITED",
        "dob": date(1999, 2, 5),
        "city": "Lalitpur",
        "country": "Nepal",
        "bio": "Architecture student who loves live music and sketching old streets.",
        "interests": ("Architecture", "Sketching", "Live music"),
    },
    {
        "key": "suman",
        "phone_number": "+9779800000106",
        "username": "suman_local_06",
        "full_name": "Suman KC",
        "gender": "MALE",
        "zodiac_sign": "SAGITTARIUS",
        "mood": "HAPPY",
        "dob": date(1998, 12, 3),
        "city": "Lalitpur",
        "country": "Nepal",
        "bio": "Community organiser, cyclist, and unapologetic board-game fan.",
        "interests": ("Cycling", "Board games", "Community events"),
    },
)


COUPLES = (
    {
        "key": "asha-bikash",
        "sender": "asha",
        "receiver": "bikash",
        "title": "Asha & Bikash",
        "relationship_tagline": "Designing a life full of little adventures.",
        "journey_story": (
            "We met over a shared table at a Kathmandu coffee shop and kept "
            "finding new places to explore together."
        ),
        "anniversary_date": date(2022, 4, 14),
        "relationship_strength": 82,
        "shared_interests": ["Coffee walks", "Road trips", "Photography"],
        "shared_dreams": ["Visit every province", "Build a calm home studio"],
    },
    {
        "key": "mira-rohan",
        "sender": "mira",
        "receiver": "rohan",
        "title": "Mira & Rohan",
        "relationship_tagline": "Slow mornings, big mountains, shared playlists.",
        "journey_story": (
            "A rainy trek changed our plans and started a friendship that grew "
            "into our favourite partnership."
        ),
        "anniversary_date": date(2021, 10, 9),
        "relationship_strength": 88,
        "shared_interests": ["Camping", "Books", "Cooking"],
        "shared_dreams": ["Open a tiny reading cafe", "Complete the Annapurna circuit"],
    },
)


PENDING_PAIR = {
    "key": "suman-nisha",
    "sender": "suman",
    "receiver": "nisha",
}


def _operation_id(scenario: str, action: str):
    return uuid5(NAMESPACE_URL, f"https://local.srisu.test/{scenario}/{action}")


class Command(BaseCommand):
    help = "Create deterministic local-only users, couples, rooms, and a pending invitation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-local",
            action="store_true",
            help="Confirm that this synthetic data should be written to the local database.",
        )

    def handle(self, *args, **options):
        if not options["confirm_local"]:
            raise CommandError("Pass --confirm-local to create the synthetic accounts.")
        if not settings.DEBUG or not settings.OTP_MOCK_DELIVERY:
            raise CommandError(
                "This command only runs with DEBUG=true and OTP mock delivery enabled."
            )
        try:
            configured_mock_code = mock_code()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        if configured_mock_code is None:
            raise CommandError("A valid OTP mock code is required for local sign-in.")

        created_users = 0
        created_couples = 0
        seeded_couple_ids = []
        with transaction.atomic():
            users = {}
            for definition in USERS:
                user, created = self._ensure_user(definition)
                users[definition["key"]] = user
                created_users += int(created)

            for definition in COUPLES:
                couple, created = self._ensure_couple(definition, users)
                self._fill_blank_couple_profile(couple, definition)
                seeded_couple_ids.append(couple.pk)
                created_couples += int(created)

            pending_state = self._ensure_pending_pair(PENDING_PAIR, users)

        matrix_states = list(
            MatrixRoomMapping.objects.filter(
                chat_room__couple_id__in=seeded_couple_ids
            ).values_list("state", flat=True)
        )
        active_matrix_rooms = matrix_states.count(MatrixRoomMapping.State.ACTIVE)
        self.stdout.write(
            self.style.SUCCESS(
                "Local test account records ready: "
                f"created_users={created_users} created_couples={created_couples} "
                f"pending_pair={pending_state} "
                f"secure_chat_active={active_matrix_rooms}/{len(seeded_couple_ids)}"
            )
        )
        for definition in USERS:
            self.stdout.write(
                f"{definition['phone_number']}  {definition['full_name']}  "
                f"role={definition['key']}"
            )
        self.stdout.write(
            "Request an OTP through the app and use the configured development mock code."
        )
        if active_matrix_rooms != len(seeded_couple_ids):
            self.stdout.write(
                self.style.WARNING(
                    "Secure chat is not ready for every seeded couple yet; "
                    "allow the Matrix reconciler to run, then check matrix_status."
                )
            )

    def _ensure_user(self, definition):
        phone_number = definition["phone_number"]
        user = UserModel.objects.filter(phone_number=phone_number).first()
        if user is not None:
            if user.username != definition["username"]:
                raise CommandError(
                    f"Reserved phone {phone_number} belongs to another local identity."
                )
            if not user.is_active:
                raise CommandError(f"Reserved local account {phone_number} is inactive.")
            if not user.is_phone_verified or not user.is_profile_complete:
                raise CommandError(
                    f"Reserved local account {phone_number} is not setup-complete."
                )
            if not user.profile_photo and not user.profile_photo_skipped:
                raise CommandError(
                    f"Reserved local account {phone_number} has unfinished photo setup."
                )
            return user, False

        if UserModel.objects.filter(username=definition["username"]).exists():
            raise CommandError(
                f"Reserved username {definition['username']} belongs to another account."
            )

        fields = {
            key: value
            for key, value in definition.items()
            if key not in {"key", "phone_number", "interests"}
        }
        user = UserModel.objects.create_user(
            phone_number=phone_number,
            is_active=True,
            is_phone_verified=True,
            is_profile_complete=True,
            profile_photo_skipped=True,
            **fields,
        )
        for interest in definition["interests"]:
            UserInterestModel.objects.get_or_create(
                user=user,
                name=interest,
                defaults={"removed": False},
            )
        return user, True

    def _ensure_couple(self, definition, users):
        sender = users[definition["sender"]]
        receiver = users[definition["receiver"]]
        memberships = list(
            CoupleMembershipModel.objects.select_related("couple__couple_connection")
            .filter(user_id__in=[sender.pk, receiver.pk])
            .order_by("user_id")
        )
        if memberships:
            couples = {membership.couple_id for membership in memberships}
            members = {membership.user_id for membership in memberships}
            if couples != {memberships[0].couple_id} or members != {sender.pk, receiver.pk}:
                raise CommandError(
                    f"Seed pair {definition['key']} conflicts with an active relationship."
                )
            couple = memberships[0].couple
            connection = couple.couple_connection
            if (
                connection is None
                or {
                    connection.sender_number,
                    connection.receiver_number,
                }
                != {sender.phone_number, receiver.phone_number}
                or connection.connection_status != CoupleConnectionStatus.ACCEPTED
                or connection.ended_at is not None
            ):
                raise CommandError(
                    f"Seed pair {definition['key']} has inconsistent relationship state."
                )
            receiver_actor = users[
                definition["receiver"]
                if connection.receiver_number == receiver.phone_number
                else definition["sender"]
            ]
            try:
                accepted = accept_connection(
                    connection_id=connection.pk,
                    actor=receiver_actor,
                    operation_id=_operation_id(definition["key"], "accept"),
                )
            except (RelationshipConflict, RelationshipPermissionDenied) as exc:
                raise CommandError(str(exc)) from exc
            return accepted.couple, False

        pair_filter = Q(
            sender_number=sender.phone_number,
            receiver_number=receiver.phone_number,
        ) | Q(
            sender_number=receiver.phone_number,
            receiver_number=sender.phone_number,
        )
        connection = (
            CoupleConnectionModel.objects.filter(pair_filter, ended_at__isnull=True)
            .filter(
                connection_status__in=[
                    CoupleConnectionStatus.PENDING,
                    CoupleConnectionStatus.ACCEPTED,
                ]
            )
            .order_by("-updated_at", "-pk")
            .first()
        )
        if connection is None:
            requested = create_connection_request(
                sender_number=sender.phone_number,
                receiver_number=receiver.phone_number,
                actor=sender,
                operation_id=_operation_id(definition["key"], "request"),
            )
            connection = requested.connection

        couple_will_be_created = not CoupleModel.objects.filter(
            couple_connection=connection
        ).exists()
        receiver_actor = (
            receiver
            if connection.receiver_number == receiver.phone_number
            else sender
        )

        try:
            accepted = accept_connection(
                connection_id=connection.pk,
                actor=receiver_actor,
                operation_id=_operation_id(definition["key"], "accept"),
            )
        except (RelationshipConflict, RelationshipPermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        return accepted.couple, couple_will_be_created

    @staticmethod
    def _fill_blank_couple_profile(couple, definition):
        changed = []
        for field in (
            "title",
            "relationship_tagline",
            "journey_story",
            "anniversary_date",
            "relationship_strength",
            "shared_interests",
            "shared_dreams",
        ):
            current = getattr(couple, field)
            if current in (None, "", [], {}):
                setattr(couple, field, definition[field])
                changed.append(field)
        if couple.profile_completed_at is None:
            couple.profile_completed_at = timezone.now()
            changed.append("profile_completed_at")
        if changed:
            couple.save(update_fields=[*changed, "updated_at"])

    def _ensure_pending_pair(self, definition, users):
        sender = users[definition["sender"]]
        receiver = users[definition["receiver"]]
        memberships = CoupleMembershipModel.objects.filter(
            user_id__in=[sender.pk, receiver.pk]
        )
        if memberships.exists():
            if (
                memberships.count() == 2
                and memberships.values("couple_id").distinct().count() == 1
            ):
                return "accepted"
            raise CommandError(
                f"Seed pair {definition['key']} conflicts with an active relationship."
            )

        current = (
            CoupleConnectionModel.objects.filter(
                Q(
                    sender_number=sender.phone_number,
                    receiver_number=receiver.phone_number,
                )
                | Q(
                    sender_number=receiver.phone_number,
                    receiver_number=sender.phone_number,
                ),
                connection_status=CoupleConnectionStatus.PENDING,
            )
            .order_by("-updated_at", "-pk")
            .first()
        )
        if current is not None:
            if current.sender_number != sender.phone_number:
                raise CommandError(
                    f"Seed pair {definition['key']} has a reversed pending invitation."
                )
            return "existing"

        requested = create_connection_request(
            sender_number=sender.phone_number,
            receiver_number=receiver.phone_number,
            actor=sender,
            operation_id=_operation_id(definition["key"], "request"),
        )
        if requested.connection.connection_status != CoupleConnectionStatus.PENDING:
            return requested.connection.connection_status.lower()
        return "existing" if requested.replayed else "created"
