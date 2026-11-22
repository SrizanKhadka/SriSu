"""Real 100-account fixtures, isolated media, and permission/rollback regressions."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
import tempfile
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.demo_accounts import MediaWrites, operation
from authentication.models import UserModel, UserPhotoAlbumModel, UserInterestModel, DeviceSession
from authentication.progress import profile_progress
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping
from notifications.models import Delivery, Event
from social.models import (CoupleModel, CoupleMembershipModel, CoupleMomentModel,
    CoupleMomentPhotoModel, CouplePlanModel, CoupleStoryAnswerModel, MomentDraft)


@override_settings(DEBUG=True, OTP_MOCK_DELIVERY=True, OTP_MOCK_CODE="123456", NOTIFICATIONS_ENABLED=True)
class DemoAccountTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="srisu-demo-tests-")
        self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)

    def seed(self, **options):
        output = StringIO()
        call_command("seed_demo_accounts", stdout=output, **({"confirm_local": True} | options))
        return output.getvalue()

    def files(self):
        return {str(p.relative_to(self.directory.name)) for p in Path(self.directory.name).rglob("*") if p.is_file()}

    def test_complete_dataset_and_no_external_work(self):
        with patch("social.services.relationship_service.schedule_matrix_room_provisioning", side_effect=AssertionError("network callback")):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                output = self.seed()
        self.assertEqual(callbacks, [])
        self.assertEqual(UserModel.objects.count(), 100)
        self.assertEqual(CoupleModel.objects.count(), 50)
        self.assertEqual(CoupleMembershipModel.objects.count(), 100)
        self.assertEqual(ChatRoom.objects.count(), 50)
        self.assertEqual(MatrixRoomMapping.objects.filter(state="pending").count(), 50)
        self.assertEqual(MatrixUserMapping.objects.count(), 100)
        self.assertEqual(UserInterestModel.objects.count(), 500)
        self.assertEqual(UserPhotoAlbumModel.objects.count(), 200)
        self.assertEqual(CoupleStoryAnswerModel.objects.count(), 300)
        self.assertEqual(CouplePlanModel.objects.count(), 150)
        self.assertEqual(CoupleMomentModel.objects.count(), 150)
        self.assertEqual(CoupleMomentPhotoModel.objects.count(), 150)
        self.assertEqual(len(self.files()), 500)
        self.assertFalse(Event.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        now = timezone.now()
        self.assertFalse(CouplePlanModel.objects.filter(created_at__gt=now).exists())
        for user in UserModel.objects.all():
            progress = profile_progress(user)
            self.assertEqual(progress["next_step"], "complete")
            self.assertEqual(progress["membership"], "linked")
            self.assertFalse(user.profile_photo_skipped)
            self.assertFalse(user.has_usable_password())
            self.assertFalse(user.is_staff or user.is_superuser)
            self.assertLessEqual(user.dob.year, now.year - 25)
        self.assertIn("created_users=100", output)
        self.assertNotIn("123456", output)

    def test_rerun_preserves_edits_and_unrelated_users_without_duplicate_files(self):
        unrelated = UserModel.objects.create_user(phone_number="+447700901111", full_name="Unrelated local account")
        self.seed()
        user = UserModel.objects.get(phone_number="+447700900000")
        UserModel.objects.filter(pk=user.pk).update(bio="My edited demo biography")
        before = self.files()
        plan_ids = set(CouplePlanModel.objects.values_list("pk", flat=True))
        moment_ids = set(CoupleMomentModel.objects.values_list("pk", flat=True))
        output = self.seed()
        self.assertIn("created_users=0 created_couples=0", output)
        user.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(user.bio, "My edited demo biography")
        self.assertEqual(unrelated.full_name, "Unrelated local account")
        self.assertFalse(unrelated.is_profile_complete)
        self.assertEqual(UserModel.objects.count(), 101)
        self.assertEqual(plan_ids, set(CouplePlanModel.objects.values_list("pk", flat=True)))
        self.assertEqual(moment_ids, set(CoupleMomentModel.objects.values_list("pk", flat=True)))
        self.assertEqual(before, self.files())

    def test_dry_run_does_not_write_database_media_or_roster(self):
        path = Path(self.directory.name) / "roster.json"
        output = self.seed(dry_run=True, confirm_local=False, manifest=path)
        self.assertIn("would create 100 users and 50 couples", output)
        self.assertFalse(UserModel.objects.exists())
        self.assertFalse(Group.objects.exists())
        self.assertEqual(self.files(), set())

    def test_requires_development_settings_and_explicit_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--confirm-local"):
            self.seed(confirm_local=False)
        for values in ({"DEBUG": False}, {"OTP_MOCK_DELIVERY": False}, {"OTP_MOCK_CODE": ""}, {"OTP_MOCK_CODE": "invalid"}):
            with override_settings(**values), self.assertRaises(CommandError):
                self.seed()
        self.assertFalse(UserModel.objects.exists())
        self.assertEqual(self.files(), set())

    def test_collision_never_claims_existing_account(self):
        user = UserModel.objects.create_user(phone_number="+447700900099", full_name="Existing identity")
        with self.assertRaisesMessage(CommandError, "belongs to another account"):
            self.seed()
        user.refresh_from_db()
        self.assertEqual(user.full_name, "Existing identity")
        self.assertEqual(UserModel.objects.count(), 1)
        self.assertFalse(Group.objects.exists())
        self.assertEqual(self.files(), set())

    def test_late_media_failure_rolls_back_database_and_new_files(self):
        original = MediaWrites.save
        calls = 0

        def fail_after_write(writer, *args):
            nonlocal calls
            result = original(writer, *args)
            calls += 1
            if calls == 307:
                raise OSError("simulated full disk")
            return result

        sentinel = Path(self.directory.name) / "existing.txt"
        sentinel.write_text("preserve me")
        with patch.object(MediaWrites, "save", fail_after_write), self.assertRaisesMessage(OSError, "simulated full disk"):
            self.seed()
        self.assertFalse(UserModel.objects.exists())
        self.assertFalse(CoupleModel.objects.exists())
        self.assertFalse(Group.objects.exists())
        self.assertEqual(self.files(), {"existing.txt"})
        self.seed()
        self.assertEqual(UserModel.objects.count(), 100)
        self.assertEqual(sentinel.read_text(), "preserve me")

    def test_foreign_relationship_and_changed_seed_fail_closed(self):
        self.seed()
        with self.assertRaisesMessage(CommandError, "different seed"):
            self.seed(seed=43)
        member = CoupleMembershipModel.objects.order_by("id").first()
        outsider = UserModel.objects.create_user(phone_number="+447700901111", full_name="Outsider")
        member.user = outsider
        member.save(update_fields=["user"])
        with self.assertRaisesMessage(CommandError, "no longer has its seeded partner"):
            self.seed()
        member.refresh_from_db()
        self.assertEqual(member.user_id, outsider.pk)
        self.assertEqual(UserModel.objects.count(), 101)

    def test_photo_integrity_failure_precedes_mutation(self):
        with patch("authentication.demo_accounts.ASSET_ROOT", Path(self.directory.name)), self.assertRaisesMessage(CommandError, "photo pack"):
            self.seed()
        self.assertFalse(UserModel.objects.exists())

    def test_seeded_profiles_and_private_media_enforce_couple_boundaries(self):
        self.seed()
        first, partner, outsider = list(UserModel.objects.order_by("phone_number")[:3])
        couple = first.couple_memberships.get().couple
        private = CoupleMomentModel.objects.get(couple=couple, visibility="private", is_archived=False)
        photo = private.photos.get()
        client = APIClient()
        for member in (first, partner):
            client.force_authenticate(member)
            response = client.get(f"/api/social/profiles/{couple.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data["data"]["story"]), 6)
            response = client.get(f"/api/social/couple-moments/{private.pk}/photos/{photo.pk}/")
            self.assertEqual(response.status_code, 200)
            # Consume the test client's closing iterator. Calling close() directly
            # sends request_finished outside its connection-signal guard on PostgreSQL.
            self.assertTrue(b"".join(response.streaming_content))
        client.force_authenticate(outsider)
        response = client.get(f"/api/social/profiles/{couple.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("story", response.data["data"])
        self.assertNotIn("song", response.data["data"])
        self.assertEqual(client.get(f"/api/social/couple-moments/{private.pk}/photos/{photo.pk}/").status_code, 404)
        self.assertEqual(client.patch(f"/api/social/profiles/{couple.pk}/sections/story/", {}, format="json").status_code, 404)
        private_couple = UserModel.objects.get(phone_number="+447700900008").couple_memberships.get().couple
        self.assertEqual(client.get(f"/api/social/profiles/{private_couple.pk}/cover/").status_code, 404)
        self.assertEqual(client.get("/media/" + str(photo.image)).status_code, 404)
        self.assertEqual(client.get("/media/" + str(private_couple.cover_photo)).status_code, 404)
        client.force_authenticate(None)
        self.assertEqual(client.get(f"/api/social/profiles/{couple.pk}/").status_code, 401)

    def test_sign_in_and_expired_session(self):
        self.seed()
        client = APIClient()
        client.credentials(HTTP_X_SRISU_CONTRACT="core-1", HTTP_X_SRISU_AUTH="auth-1")
        phone = "+447700900000"
        issued = client.post("/api/auth/send-otp/", {"phone_number": phone, "request_id": str(uuid4())}, format="json")
        self.assertEqual(issued.status_code, 200)
        verified = client.post("/api/auth/verify-otp/", {"phone_number": phone,
            "challenge_id": issued.data["data"]["challenge_id"], "otp_code": "123456"}, format="json")
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.data["data"]["progress"]["membership"], "linked")
        client.credentials(HTTP_AUTHORIZATION="Bearer " + verified.data["data"]["tokens"]["access"],
            HTTP_X_SRISU_CONTRACT="core-1", HTTP_X_SRISU_AUTH="auth-1")
        self.assertEqual(client.get("/api/social/profiles/me/").status_code, 200)
        DeviceSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(client.get("/api/social/profiles/me/").status_code, 401)

    def test_refresh_updates_only_current_seeded_activity(self):
        self.seed()
        public = MomentDraft.objects.get(pk=operation(42, "couple/1/moment/0")).moment
        archive = MomentDraft.objects.get(pk=operation(42, "couple/1/moment/2")).moment
        archive_expiry = archive.expires_at
        plan = CouplePlanModel.objects.get(request_id=operation(42, "couple/1/plan/1"))
        old_start = plan.starts_at
        edited = CouplePlanModel.objects.get(request_id=operation(42, "couple/2/plan/1"))
        edited.starts_at += timedelta(days=2)
        edited.save(update_fields=["starts_at"])
        edited_start = edited.starts_at
        later = timezone.now() + timedelta(days=4)
        with patch("django.utils.timezone.now", return_value=later):
            self.seed(refresh_activity=True)
        public.refresh_from_db()
        archive.refresh_from_db()
        plan.refresh_from_db()
        self.assertGreater(public.expires_at, later)
        self.assertEqual(archive.expires_at, archive_expiry)
        self.assertGreater(plan.starts_at, old_start)
        edited.refresh_from_db()
        self.assertEqual(edited.starts_at, edited_start)
        self.assertEqual(CoupleMomentModel.objects.count(), 150)

    def test_roster_is_complete_and_existing_files_are_protected(self):
        path = Path(self.directory.name) / "roster.json"
        path.write_text('{"unrelated": true}')
        with self.assertRaisesMessage(CommandError, "Refusing to overwrite"):
            self.seed(manifest=path)
        path.unlink()
        self.seed(manifest=path)
        roster = json.loads(path.read_text())
        self.assertEqual(len(roster["accounts"]), 100)
        self.assertEqual(roster["counts"]["couples"], 50)
        self.assertNotIn("123456", path.read_text())
        self.seed(manifest=path)
        self.assertEqual(json.loads(path.read_text())["created_users"], 0)


@override_settings(DEBUG=True, OTP_MOCK_DELIVERY=True, OTP_MOCK_CODE="123456")
class ConcurrentDemoAccountTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_seeds_share_one_complete_dataset(self):
        with tempfile.TemporaryDirectory(prefix="srisu-demo-concurrent-") as directory:
            with override_settings(MEDIA_ROOT=directory):
                barrier = Barrier(2)

                def seed():
                    connections.close_all()
                    try:
                        barrier.wait(timeout=15)
                        output = StringIO()
                        call_command("seed_demo_accounts", confirm_local=True, stdout=output)
                        return output.getvalue()
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(seed) for _ in range(2)]
                    outputs = [f.result(timeout=120) for f in futures]
                self.assertEqual(sum("created_users=100" in s for s in outputs), 1)
                self.assertEqual(sum("created_users=0 " in s for s in outputs), 1)
                self.assertEqual(UserModel.objects.count(), 100)
                self.assertEqual(CoupleModel.objects.count(), 50)
                self.assertEqual(CoupleMembershipModel.objects.count(), 100)
                self.assertEqual(CoupleMomentModel.objects.count(), 150)
                self.assertEqual(sum(p.is_file() for p in Path(directory).rglob("*")), 500)
