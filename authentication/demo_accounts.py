"""Offline demo fixtures using the current relationship and privacy boundaries.

This deliberately hydrates historical records rather than replaying live requests:
no OTP, notification delivery, cache invalidation or Matrix HTTP callbacks run.
The existing membership/chat services still enforce the relationship graph.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from PIL import Image
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from authentication import demo_data as content
from authentication.models import UserModel, UserInterestModel, InterestModel, UserPhotoAlbumModel
from chat.models import MatrixRoomMapping
from chat.services.matrix_provisioning import ensure_matrix_mapping_records
from notifications.models import Preferences
from social.models import (
    CoupleConnectionModel, CoupleMembershipModel, CoupleModel, CoupleStoryAnswerModel,
    CoupleSongModel, CouplePrivacyConsentModel, CoupleProfileChangeModel, CouplePlanModel,
    CoupleMomentModel, CoupleMomentPhotoModel, CoupleMomentNoteModel, CoupleMomentViewModel,
    MomentDraft, MomentReaction, CoupleFaveModel, CoupleFaveVersion, UserPreferenceModel,
    CoupleStatus, DateIdea,
)
from social.services.couple_profile_service import create_or_get_couple_for_connection
from social.services.relationship_service import ensure_relationship_chat_room
from social.services.couple_profile_sections import audience, digest, raw_sections
from social.services.moment_publication import publication_hash
from social.services.plans import payload

ASSET_ROOT = Path(__file__).with_name("demo_assets")
DATASET = "srisu-demo-v1"


def operation(seed, key):
    return uuid5(NAMESPACE_URL, f"https://demo.srisu.test/v1/{seed}/{key}")


def load_assets():
    """Fail before mutation if the checked-in photo pack is missing/corrupt."""
    try:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        people, scenes = manifest["people"], manifest["scenes"]
        if manifest["version"] != 1 or len(people) != 100 or len(scenes) != 6:
            raise ValueError("Expected 100 people and six scenes.")
        for entry in people + scenes:
            path = (ASSET_ROOT / entry["file"]).resolve()
            if path.parent != ASSET_ROOT.resolve():
                raise ValueError("Invalid asset path.")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"Checksum mismatch: {path.name}")
            with Image.open(path) as photo:
                if photo.format not in {"JPEG", "PNG", "WEBP"} or photo.width * photo.height > 20_000_000:
                    raise ValueError("Invalid photo format or size.")
                entry["dimensions"] = photo.size
                photo.verify()
            entry["bytes"] = data
        return people, scenes
    except (OSError, KeyError, ValueError) as exc:
        raise CommandError(f"Demo photo pack is unavailable or invalid: {exc}") from exc


class MediaWrites:
    def __init__(self, seed):
        if not isinstance(default_storage, FileSystemStorage):
            raise CommandError("Demo seeding requires local FileSystemStorage; remote media is not supported.")
        self.seed = seed
        self.created = set()

    def save(self, prefix, key, asset):
        name = f"{prefix}/demo_v1/{self.seed}/{key}{Path(asset['file']).suffix}"
        if default_storage.exists(name):
            with default_storage.open(name, "rb") as existing:
                if hashlib.sha256(existing.read()).hexdigest() != asset["sha256"]:
                    raise CommandError(f"Refusing to overwrite a different media file: {name}")
            return name
        # Include the intended name so write-then-raise storage failures compensate.
        self.created.add(name)
        saved = default_storage.save(name, ContentFile(asset["bytes"]))
        self.created.add(saved)
        if saved != name:
            raise CommandError("Demo media changed concurrently; retry after the other writer finishes.")
        return saved

    def rollback(self):
        failures = []
        for name in self.created:
            try:
                default_storage.delete(name)
            except OSError:
                failures.append(name)
        return failures


class DemoAccounts:
    def __init__(self, seed=42, refresh_activity=False):
        self.seed = seed
        self.refresh_activity = refresh_activity
        self.now = timezone.now()
        self.people, self.scenes = load_assets()
        self.definitions = content.definitions(seed, timezone.localdate(), self.people)
        self.group_name = f"{DATASET}:seed={seed}"
        self.media = MediaWrites(seed)

    def preflight(self):
        """Read only; used by dry-run and repeated under the dataset write lock."""
        if Group.objects.filter(name__startswith=f"{DATASET}:seed=").exclude(name=self.group_name).exists():
            raise CommandError("This demo dataset already uses a different seed. Reuse its original --seed.")
        usernames = [d["username"] for d in self.definitions]
        phones = [d["phone_number"] for d in self.definitions]
        candidates = list(UserModel.objects.filter(Q(phone_number__in=phones) | Q(username__in=usernames)))
        owned = {u.pk: u for u in UserModel.objects.filter(groups__name=self.group_name)}
        by_phone = {u.phone_number: u for u in candidates}
        if any(u.pk not in owned for u in candidates):
            raise CommandError("A reserved demo phone or username belongs to another account; nothing was changed.")
        if owned and (len(owned) != 100 or len(candidates) != 100):
            raise CommandError("The existing demo dataset is incomplete. Restore it before rerunning; deleted identities are not recreated.")
        for definition in self.definitions:
            user = by_phone.get(definition["phone_number"])
            if user and (user.email != definition["email"] or user.username != definition["username"]):
                raise CommandError("A demo identity was renamed or reassigned. Restore its reserved phone, username and email before rerunning.")
            if user and (not user.is_active or user.is_staff or user.is_superuser):
                raise CommandError("Demo accounts must remain active, non-staff users. Nothing was changed.")
        # Never consume existing invitations, historical relationships, or real partners.
        expected = {operation(self.seed, f"couple/{i}/request") for i in range(1, 51)}
        connections = CoupleConnectionModel.objects.filter(Q(sender_number__in=phones) | Q(receiver_number__in=phones))
        if connections.exclude(request_operation_id__in=expected).exists():
            raise CommandError("A demo identity has a relationship outside this dataset. Nothing was changed.")
        if not owned and connections.exists():
            raise CommandError("Reserved demo numbers have existing relationship history. Nothing was changed.")
        if owned:
            self.verify(list(owned.values()))
        return len(owned)

    def run(self):
        try:
            with transaction.atomic():
                # All seeds share this durable row. PostgreSQL serializes concurrent
                # runs; SQLite's single-writer lock fails closed instead of duplicating.
                lock, _ = Group.objects.get_or_create(name=DATASET)
                Group.objects.select_for_update().get(pk=lock.pk)
                existing = self.preflight()
                group, _ = Group.objects.get_or_create(name=self.group_name)
                if group.permissions.exists():
                    raise CommandError("The demo ownership group must not grant permissions.")
                if existing:
                    users = list(UserModel.objects.filter(groups=group).order_by("phone_number"))
                else:
                    users = [self.create_user(d, group) for d in self.definitions]
                    for i in range(50):
                        self.create_couple(i + 1, users[2*i:2*i+2], self.definitions[2*i])
                    self.create_feed_activity(users)
                if self.refresh_activity:
                    self.refresh_recent_activity()
                self.verify(users)
                result = self.report(users, created=100 - existing)
            return result
        except Exception as exc:
            failures = self.media.rollback()
            if failures:
                raise CommandError("Database rolled back, but some new demo files could not be removed: " + ", ".join(sorted(failures))) from exc
            raise

    def create_user(self, definition, group):
        d = definition
        index = d["index"]
        portrait = self.people[index - 1]
        scene = self.scenes[(index - 1) % 6]
        fields = {k: v for k, v in d.items() if k not in {"index", "pair", "zone", "interests", "anniversary"}}
        user = UserModel.objects.create_user(
            **fields, is_active=True, is_phone_verified=True, is_profile_complete=True,
            profile_photo_skipped=False, is_engaged=True,
            profile_photo=self.media.save("profiles", f"user{index:03d}", portrait),
        )
        user.groups.add(group)
        joined = self.now - timedelta(days=120 + index)
        UserModel.objects.filter(pk=user.pk).update(date_joined=joined, created_date=joined)
        for name in d["interests"]:
            item = InterestModel.objects.filter(name__iexact=name).first()
            UserInterestModel.objects.create(user=user, name=item.name if item else name, interest=item)
        for slot, asset in enumerate((portrait, scene)):
            UserPhotoAlbumModel.objects.create(user=user, photo=self.media.save("user_album", f"user{index:03d}_{slot}", asset))
        UserPreferenceModel.objects.create(user=user, min_age=24, max_age=70,
            zodiac_sign=user.zodiac_sign, radius_km=30, city=user.city, country=user.country)
        Preferences.objects.create(user=user, push=False, chat=True, activity=True)
        return user

    def create_couple(self, pair, users, definition):
        first, second = users
        connection = CoupleConnectionModel.objects.create(
            sender_number=first.phone_number, receiver_number=second.phone_number,
            connection_status="ACCEPTED", accepted_at=self.now - timedelta(days=90), revision=2,
            request_operation_id=operation(self.seed, f"couple/{pair}/request"),
            acceptance_operation_id=operation(self.seed, f"couple/{pair}/accept"),
        )
        CoupleConnectionModel.objects.filter(pk=connection.pk).update(created_at=self.now - timedelta(days=91))
        couple = create_or_get_couple_for_connection(connection)
        room = ensure_relationship_chat_room(couple)
        ensure_matrix_mapping_records(room)  # Durable pending work, no network callback.
        members = list(couple.memberships.order_by("id"))
        audience_ids = [m.pk for m in members]
        for member in members:
            member.nickname = member.user.first_name
            member.save(update_fields=["nickname"])
        couple.title = f"{first.first_name} & {second.first_name}"
        couple.anniversary_date = definition["anniversary"]
        couple.relationship_tagline = content.TAGLINES[(pair - 1) % len(content.TAGLINES)]
        meeting = content.MEETINGS[(pair - 1) % len(content.MEETINGS)]
        couple.journey_story = (
            f"We met at {meeting} in {first.city}. A short conversation turned into a long walk, "
            f"then a second invitation neither of us wanted to miss. These days, {first.first_name} "
            f"brings the ideas and {second.first_name} finds the scenic route. We make time for "
            "Sunday breakfast, honest check-ins, and celebrating the little things."
        )
        couple.relationship_strength = 76 + pair % 22
        couple.shared_interests = definition["interests"][:3]
        couple.shared_dreams = [content.DREAMS[(pair - 1) % 8], content.DREAMS[(pair + 2) % 8]]
        couple.cover_photo = self.media.save("couples/profile_private", f"couple{pair:02d}", self.scenes[(pair - 1) % 6])
        couple.profile_completed_at = self.now - timedelta(days=88)
        couple.save()
        CoupleModel.objects.filter(pk=couple.pk).update(created_at=self.now - timedelta(days=90))
        couple.memberships.update(joined_at=self.now - timedelta(days=90))
        for index, user in enumerate(users):
            answers = {
                "how_met": f"At {meeting} in {first.city}. " + ("We stayed talking long after everyone else left." if index == 0 else "I still remember how easily the conversation flowed."),
                "first_move": (f"I asked {second.first_name} to meet for coffee the following weekend." if index == 0 else f"{first.first_name} suggested coffee. I suggested making it a whole afternoon."),
                "first_impression": "Warm, curious, thoughtful" if index == 0 else "Kind, funny, adventurous",
            }
            for prompt, answer in answers.items():
                CoupleStoryAnswerModel.objects.create(couple=couple, author=user, prompt=prompt, answer=answer)
            CoupleProfileChangeModel.objects.create(couple=couple, actor=user, section="story")
            CoupleStatus.objects.create(couple=couple, author=user, audience_membership_ids=audience_ids,
                text="Looking forward to our walk" if index == 0 else "Grateful for our little routine",
                expires_at=self.now + timedelta(hours=20), revision=1,
                request_id=operation(self.seed, f"couple/{pair}/status/{index}"))
        title, artist, band = content.SONGS[(pair - 1) % len(content.SONGS)]
        CoupleSongModel.objects.create(couple=couple, title=title, artist=artist, band=band,
            picked_by=users[pair % 2], note="The song we always come back to on the way home.")
        for slot in range(3):
            self.create_plan(pair, slot, couple, users, audience_ids, definition["zone"])
            self.create_moment(pair, slot, couple, users, members)
        DateIdea.objects.create(id=operation(self.seed, f"couple/{pair}/idea"), couple=couple,
            creator=first, audience_membership_ids=audience_ids, preset=("coffee", "walk", "movie")[pair % 3],
            interested_by=[first.pk, second.pk], revision=2)
        # Some couples publish only identity/date/interests/cover. Stories and song
        # stay private. Use the exact content- and membership-bound consent format.
        if pair % 5:
            sections = ["identity", "date", "interests", "cover"]
            raw = raw_sections(couple, members, first)
            couple.privacy_proposal_version = 1
            couple.privacy_proposal = {"sections": sections, "audience": audience(members),
                "fingerprints": {s: digest(raw[s]) for s in sections}}
            couple.save(update_fields=["privacy_proposal", "privacy_proposal_version"])
            for user in users:
                CouplePrivacyConsentModel.objects.create(couple=couple, user=user, proposal_version=1)
        return couple

    def create_plan(self, pair, slot, couple, users, member_ids, zone):
        starts = self.now + timedelta(days=(-14, 3, 8)[slot], hours=pair % 6)
        titles = ("Our Sunday breakfast", "An evening walk and dinner", "A slow weekend by the water")
        values = dict(title=titles[slot], starts_at=starts, place=("Our kitchen", f"{users[0].city} riverside", "Our favourite picnic spot")[slot], time_zone=zone)
        CouplePlanModel.objects.create(
            couple=couple, created_by=users[slot % 2], request_id=operation(self.seed, f"couple/{pair}/plan/{slot}"),
            audience_membership_ids=member_ids, **values, creation_payload=payload(values),
            response="pending" if slot == 2 else "yes", revision=1 if slot == 2 else 2,
            response_note="I'll bring something homemade." if slot == 1 else "",
            completed=slot == 0, completed_at=starts + timedelta(hours=2) if slot == 0 else None,
            completed_by=users[1] if slot == 0 else None,
            checklist=[] if slot == 2 else [{"id": str(operation(self.seed, f"couple/{pair}/checklist/{slot}")),
                "text": "Bring a camera" if slot == 1 else "Make fresh coffee", "assigned_to": users[0].pk,
                "checked": slot == 0, "checked_by": users[0].pk if slot == 0 else None}],
        )
        CouplePlanModel.objects.filter(couple=couple, request_id=operation(self.seed, f"couple/{pair}/plan/{slot}")).update(
            created_at=self.now - timedelta(days=18 if slot == 0 else 2))

    def create_moment(self, pair, slot, couple, users, members):
        asset = self.scenes[(pair + slot - 1) % 6]
        name = self.media.save("couples/moments", f"couple{pair:02d}_{slot}", asset)
        posted = self.now - (timedelta(days=12) if slot == 2 else timedelta(hours=2 + pair % 10 + slot))
        captions = (
            "Saving a little room in the week for a view like this. The best part was having you beside me.",
            "A small reminder of our favourite kind of afternoon: no rush, a long conversation, and a little fresh air.",
            "One for our collection of ordinary days that turned into favourite memories.",
        )
        member_ids, user_ids = [m.pk for m in members], [m.user_id for m in members]
        moment = CoupleMomentModel.objects.create(
            couple=couple, created_by=users[slot % 2], title=("A little escape", "Just the two of us", "A day worth keeping")[slot],
            caption=captions[slot], moment_date=posted.date(), mood="grateful",
            location_name="Our weekend escape", visibility="public" if slot == 0 else "private",
            tags=["weekend", "together"], partner_memory="The unplanned stops were my favourite part.",
            audience_membership_ids=member_ids, audience_user_ids=user_ids, is_archived=slot == 2,
        )
        CoupleMomentModel.objects.filter(pk=moment.pk).update(created_at=posted, expires_at=posted + timedelta(hours=24))
        photo_id = operation(self.seed, f"couple/{pair}/moment/{slot}/photo")
        CoupleMomentPhotoModel.objects.create(moment=moment, image=name, order=0,
            width=asset["dimensions"][0], height=asset["dimensions"][1])
        MomentDraft.objects.create(id=operation(self.seed, f"couple/{pair}/moment/{slot}"),
            owner=moment.created_by, couple=couple, audience_membership_ids=member_ids, audience_user_ids=user_ids,
            moment=moment, created_at=posted, published_at=posted,
            publication_hash=publication_hash(dict(caption=moment.caption, visibility=moment.visibility, photo_ids=[photo_id])),
        )
        note = CoupleMomentNoteModel.objects.create(moment=moment, sender=users[(slot + 1) % 2],
            message="This still makes me smile. Let's make time for another afternoon like it.",
            recipient_membership_ids=member_ids, recipient_user_ids=user_ids)
        CoupleMomentNoteModel.objects.filter(pk=note.pk).update(created_at=posted + timedelta(minutes=20))
        MomentReaction.objects.create(moment=moment, user=users[(slot + 1) % 2], created_at=posted + timedelta(minutes=15))
        CoupleMomentViewModel.objects.create(moment=moment, viewer=users[(slot + 1) % 2])

    def create_feed_activity(self, users):
        for pair in range(50):
            next_draft = MomentDraft.objects.get(pk=operation(self.seed, f"couple/{(pair + 1) % 50 + 1}/moment/0"))
            for user in users[pair*2:pair*2+2]:
                CoupleFaveModel.objects.create(user=user, couple=next_draft.couple)
                CoupleFaveVersion.objects.create(user=user, version=1)
                MomentReaction.objects.create(moment=next_draft.moment, user=user)
                CoupleMomentViewModel.objects.create(moment=next_draft.moment, viewer=user)

    def refresh_recent_activity(self):
        for pair in range(1, 51):
            for slot in (0, 1):
                draft = MomentDraft.objects.filter(pk=operation(self.seed, f"couple/{pair}/moment/{slot}")).first()
                if draft and draft.moment_id:
                    posted = self.now - timedelta(hours=2 + pair % 10 + slot)
                    CoupleMomentModel.objects.filter(pk=draft.moment_id, is_archived=False, is_time_capsule=False).update(
                        created_at=posted, expires_at=posted + timedelta(hours=24), moment_date=timezone.localdate())
            for slot in (1, 2):
                plan = CouplePlanModel.objects.filter(request_id=operation(self.seed, f"couple/{pair}/plan/{slot}"),
                    completed=False, cancelled_at__isnull=True, response__in=["yes", "pending"]).first()
                if plan and not plan.pending_change and plan.creation_payload == payload(dict(title=plan.title,
                        starts_at=plan.starts_at, place=plan.place, time_zone=plan.time_zone)):
                    plan.starts_at = self.now + timedelta(days=3 if slot == 1 else 8, hours=pair % 6)
                    plan.creation_payload = payload(dict(title=plan.title, starts_at=plan.starts_at, place=plan.place, time_zone=plan.time_zone))
                    plan.revision += 1
                    plan.save(update_fields=["starts_at", "creation_payload", "revision"])

    def verify(self, users):
        for index, user in enumerate(sorted(users, key=lambda u: u.phone_number), 1):
            if not all((user.full_name, user.username, user.email, user.bio, user.city, user.country,
                        user.dob, user.gender, user.zodiac_sign, user.mood, user.profile_photo,
                        user.is_profile_complete, user.is_phone_verified, user.is_engaged)) or user.profile_photo_skipped:
                raise CommandError(f"Demo account {index} has incomplete profile data.")
            if not user.user_interests.filter(removed=False).exists() or not user.user_photos.filter(removed=False).exists():
                raise CommandError(f"Demo account {index} is missing interests or album photos.")
            expected = operation(self.seed, f"couple/{(index - 1)//2 + 1}/request")
            membership = CoupleMembershipModel.objects.filter(user=user).select_related("couple__couple_connection").first()
            if membership is None:
                raise CommandError(f"Demo account {index} no longer has its seeded partner.")
            couple = membership.couple
            connection = couple.couple_connection
            if connection is None or connection.request_operation_id != expected or connection.connection_status != "ACCEPTED" or connection.ended_at:
                raise CommandError("An existing demo relationship changed. Nothing was overwritten.")
            partner_index = index + 1 if index % 2 else index - 1
            expected_phones = {user.phone_number, self.definitions[partner_index - 1]["phone_number"]}
            if set(couple.memberships.values_list("user__phone_number", flat=True)) != expected_phones or {connection.sender_number, connection.receiver_number} != expected_phones:
                raise CommandError("An existing demo relationship has different partners.")
            if not all((couple.anniversary_date, couple.title, couple.journey_story, couple.cover_photo, couple.profile_completed_at)):
                raise CommandError("An existing demo couple profile is incomplete.")
            if couple.story_answers.count() != 6 or not CoupleSongModel.objects.filter(couple=couple).exists():
                raise CommandError("An existing demo couple is missing its story answers or song.")
            for photo in [user.profile_photo, *[p.photo for p in user.user_photos.filter(removed=False)], couple.cover_photo]:
                if not photo.storage.exists(photo.name):
                    raise CommandError(f"A demo media file is missing: {photo.name}. Restore it before rerunning.")
            if not MatrixRoomMapping.objects.filter(chat_room__couple=couple).exists():
                raise CommandError("A demo couple is missing its chat provisioning record.")

    def report(self, users, created):
        accounts = []
        for user in users:
            membership = CoupleMembershipModel.objects.get(user=user)
            partner = membership.couple.memberships.exclude(user=user).select_related("user").get().user
            accounts.append(dict(id=user.pk, name=user.full_name, username=user.username, phone=user.phone_number,
                couple_id=membership.couple_id, partner_id=partner.pk, partner_name=partner.full_name,
                partner_phone=partner.phone_number))
        couple_ids = {a["couple_id"] for a in accounts}
        return dict(dataset=DATASET, seed=self.seed, created_users=created, created_couples=created // 2,
            counts=dict(users=len(users), couples=len(couple_ids),
                album_photos=UserPhotoAlbumModel.objects.filter(user__in=users, removed=False).count(),
                story_answers=CoupleStoryAnswerModel.objects.filter(couple_id__in=couple_ids).count(),
                plans=CouplePlanModel.objects.filter(couple_id__in=couple_ids).count(),
                moments=CoupleMomentModel.objects.filter(couple_id__in=couple_ids).count(),
                secure_chat_active=MatrixRoomMapping.objects.filter(chat_room__couple_id__in=couple_ids, state="active").count()),
            accounts=accounts)
