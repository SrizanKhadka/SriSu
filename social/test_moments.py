from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from authentication.models import UserModel
from social.models import (CoupleConnectionModel, CoupleMembershipModel, CoupleMomentModel,
    CoupleMomentPhotoModel, CoupleMomentNoteModel, MomentFileDeletion, SingleConnectionModel)
from social.services.couple_profile_service import create_or_get_couple_for_connection


class MomentAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.users = [UserModel.objects.create_user(phone_number=f"+97798000000{i:02}", full_name=f"User {i}") for i in range(5)]
        self.creator, self.partner, self.sender, self.other, self.new_partner = self.users
        self.connection = CoupleConnectionModel.objects.create(sender_number=self.creator.phone_number,
            receiver_number=self.partner.phone_number, connection_status="ACCEPTED")
        self.couple = create_or_get_couple_for_connection(self.connection)
        self.url = reverse("couple_moments-list")
        self.client.force_authenticate(self.creator)

    @staticmethod
    def photo(name="photo.png", fmt="PNG"):
        data = BytesIO()
        Image.new("RGB", (8, 8), "red").save(data, format=fmt)
        return SimpleUploadedFile(name, data.getvalue(), content_type="image/png")

    def create(self, **kwargs):
        data = {"caption": "Coffee together", "visibility": "public"}
        data.update(kwargs)
        response = self.client.post(self.url, data, format="multipart" if "photos" in data else "json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["data"]

    def detail(self, moment):
        return reverse("couple_moments-detail", args=[moment["id"]])

    def note(self, moment):
        self.client.force_authenticate(self.sender)
        response = self.client.post(reverse("couple_moments-notes", args=[moment["id"]]), {"message": "So happy for you!"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["data"]

    def test_both_partners_create_and_ownership_is_server_derived(self):
        for user in [self.creator, self.partner]:
            self.client.force_authenticate(user)
            moment = self.create(created_by=self.other.pk, expires_at="2099-01-01T00:00:00Z")
            self.assertEqual(moment["created_by"], user.pk)
            obj = CoupleMomentModel.objects.get(pk=moment["id"])
            self.assertEqual(obj.expires_at - obj.created_at, timedelta(hours=24))

    def test_only_creator_can_modify(self):
        moment = self.create()
        self.client.force_authenticate(self.partner)
        self.assertFalse(self.client.get(self.detail(moment)).data["can_edit"])
        for method in [self.client.put, self.client.patch, self.client.delete]:
            self.assertEqual(method(self.detail(moment), {"caption": "Changed"}, format="json").status_code, 403)
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.patch(self.detail(moment), {"caption": "Changed"}, format="json").status_code, 200)
        self.assertEqual(self.client.delete(self.detail(moment)).status_code, 200)

    def test_foreign_couple_and_inactive_couple_cannot_create(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url, {"couple": self.couple.pk, "caption": "No"}, format="json").status_code, 403)
        self.client.force_authenticate(self.creator)
        self.connection.connection_status = "BREAK-UP"
        self.connection.save()
        self.assertEqual(self.client.post(self.url, {"caption": "No"}, format="json").status_code, 400)

    def test_photo_limits_and_failed_update_preserves_everything(self):
        response = self.client.post(self.url, {"photos": [self.photo() for _ in range(6)]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CoupleMomentModel.objects.exists())
        moment = self.create(photos=[self.photo() for _ in range(5)])
        ids = [photo["id"] for photo in moment["photos"]]
        response = self.client.patch(self.detail(moment), {"caption": "Should roll back",
            "deleted_photo_ids": f"[{ids[0]}]", "photos": [self.photo(), self.photo()]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        obj = CoupleMomentModel.objects.get(pk=moment["id"])
        self.assertEqual(obj.caption, "Coffee together")
        self.assertEqual(list(obj.photos.values_list("id", flat=True)), ids)
        response = self.client.patch(self.detail(moment), {"deleted_photo_ids": f"[{ids[1]}]", "photos": [self.photo()]}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([p["order"] for p in response.data["data"]["photos"]], list(range(5)))

    def test_replace_photos_and_invalid_references(self):
        first = self.create(photos=[self.photo()])
        second = self.create(photos=[self.photo()])
        photo_id = second["photos"][0]["id"]
        for ids in [[photo_id], [99999], [True], ["1"], "bad", [1, 1]]:
            response = self.client.patch(self.detail(first), {"deleted_photo_ids": ids}, format="json")
            self.assertEqual(response.status_code, 400)
        response = self.client.patch(self.detail(first), {"replace_photos": True, "photos": [self.photo(), self.photo()]}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["data"]["photos"]), 2)
        self.assertTrue(CoupleMomentPhotoModel.objects.filter(pk=photo_id).exists())

    def test_image_validation_and_empty_content(self):
        for payload in [{}, {"caption": "   "}, {"photos": ["https://example.com/image.jpg"]}]:
            self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)
        for upload in [SimpleUploadedFile("fake.png", b"not an image"), self.photo("image.gif", "GIF")]:
            self.assertEqual(self.client.post(self.url, {"photos": [upload]}, format="multipart").status_code, 400)
        with override_settings(MOMENT_MAX_IMAGE_BYTES=10):
            self.assertEqual(self.client.post(self.url, {"photos": [self.photo()]}, format="multipart").status_code, 400)
        self.assertEqual(self.create(caption="", photos=[self.photo()])["caption"], "")

    def test_expiry_boundary_and_edit_does_not_extend(self):
        moment = self.create(photos=[self.photo()])
        obj = CoupleMomentModel.objects.get(pk=moment["id"])
        expiry = obj.expires_at
        self.client.patch(self.detail(moment), {"caption": "New", "expires_at": "2099-01-01T00:00:00Z"}, format="json")
        obj.refresh_from_db()
        self.assertEqual(expiry, obj.expires_at)
        with patch("django.utils.timezone.now", return_value=expiry - timedelta(microseconds=1)):
            self.assertEqual(self.client.get(self.detail(moment)).status_code, 200)
        with patch("django.utils.timezone.now", return_value=expiry):
            self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
            self.assertEqual(self.client.get(self.url).data["data"]["count"], 0)
            self.assertEqual(self.client.get(moment["photos"][0]["image"]).status_code, 404)
        self.assertTrue(CoupleMomentModel.objects.filter(pk=obj.pk).exists())

    def test_pagination_ties_filter_and_visibility(self):
        moments = [self.create() for _ in range(3)]
        CoupleMomentModel.objects.update(created_at=timezone.now())
        response = self.client.get(self.url, {"page_size": 2, "couple": self.couple.pk})
        data = response.data["data"]
        self.assertEqual(data["count"], 3)
        self.assertEqual([m["id"] for m in data["results"]], [moments[2]["id"], moments[1]["id"]])
        self.assertEqual(self.client.get(data["next"]).data["data"]["results"][0]["id"], moments[0]["id"])
        private = self.create(visibility="private")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.detail(private)).status_code, 404)
        self.assertEqual(self.client.get(self.url).data["data"]["count"], 3)
        self.assertEqual(self.client.get(self.url, {"couple": "invalid"}).status_code, 400)
        self.assertEqual(self.client.get(self.url + "99999999999999999999999999999/").status_code, 404)

    def test_notes_visibility_immutability_and_expiry(self):
        moment = self.create()
        note = self.note(moment)
        url = reverse("moment_notes-detail", args=[note["id"]])
        listing = reverse("moment_notes-list")
        for user in [self.creator, self.partner, self.sender]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.get(listing).data["data"]["count"], 1)
            self.assertEqual(self.client.patch(url, {"message": "edit"}, format="json").status_code, 405)
        self.assertEqual(self.client.get(self.detail(moment)).data.get("notes"), None)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get(listing).data["data"]["count"], 0)
        CoupleMomentModel.objects.filter(pk=moment["id"]).update(expires_at=timezone.now())
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(reverse("couple_moments-notes", args=[moment["id"]]), {"message": "Late"}).status_code, 404)
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_note_validation_own_couple_and_throttling(self):
        moment = self.create()
        url = reverse("couple_moments-notes", args=[moment["id"]])
        self.assertEqual(self.client.post(url, {"message": "Self"}, format="json").status_code, 400)
        self.client.force_authenticate(self.sender)
        for message in ["", " \n ", "a" * 1001]:
            self.assertEqual(self.client.post(url, {"message": message}, format="json").status_code, 400)
        cache.clear()
        with patch("social.api.moment_views.MomentNoteThrottle.rate", "1/hour"):
            self.assertEqual(self.client.post(url, {"message": "Hello"}, format="json").status_code, 201)
            self.assertEqual(self.client.post(url, {"message": "Again"}, format="json").status_code, 429)

    def test_membership_replacement_cannot_inherit_moments_or_notes(self):
        moment = self.create()
        note = self.note(moment)
        url = reverse("moment_notes-detail", args=[note["id"]])
        membership = CoupleMembershipModel.objects.get(user=self.partner)
        position = membership.position
        membership.delete()
        CoupleMembershipModel.objects.create(couple=self.couple, user=self.new_partner, position=position)
        for user in [self.partner, self.new_partner]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)

    def test_reassigned_membership_row_cannot_inherit_notes(self):
        moment = self.create()
        note = self.note(moment)
        CoupleMembershipModel.objects.filter(user=self.partner).update(user=self.new_partner)
        self.connection.receiver_number = self.new_partner.phone_number
        self.connection.save()
        self.client.force_authenticate(self.new_partner)
        self.assertEqual(self.client.get(reverse("moment_notes-detail", args=[note["id"]])).status_code, 404)
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)

    def test_couple_reassignment_empty_update_and_private_notes(self):
        moment = self.create(visibility="private", caption="", photos=[self.photo()])
        self.assertEqual(self.client.patch(self.detail(moment), {"couple": 999}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(self.detail(moment), {"replace_photos": True}, format="json").status_code, 400)
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.post(reverse("couple_moments-notes", args=[moment["id"]]), {"message": "No"}, format="json").status_code, 404)

    def test_breakup_deactivation_and_blocking(self):
        moment = self.create()
        note = self.note(moment)
        SingleConnectionModel.objects.create(sender_number=self.partner.phone_number,
            receiver_number=self.sender.phone_number, connection_status="BLOCKED")
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
        self.assertEqual(self.client.get(reverse("moment_notes-detail", args=[note["id"]])).status_code, 404)
        self.client.force_authenticate(self.creator)
        self.partner.is_active = False
        self.partner.save()
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
        self.partner.is_active = True
        self.partner.save()
        self.connection.connection_status = "BREAK-UP"
        self.connection.save()
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
        self.assertEqual(self.client.get(reverse("moment_notes-detail", args=[note["id"]])).status_code, 404)

    def test_failed_database_write_cleans_uploads(self):
        with self.captureOnCommitCallbacks(execute=True):
            with patch.object(CoupleMomentPhotoModel, "save", side_effect=DatabaseError("injected")):
                with self.assertRaises(DatabaseError):
                    self.client.post(self.url, {"photos": [self.photo()]}, format="multipart")
        self.assertFalse(CoupleMomentModel.objects.exists())
        self.assertFalse(list(Path(self.media.name).rglob("*.png")))

    def test_storage_failure_rolls_back_update_and_cleans_previous_upload(self):
        moment = self.create(photos=[self.photo()])
        storage = CoupleMomentPhotoModel._meta.get_field("image").storage
        original_save = storage.save
        calls = 0

        def fail_second(name, content, **kwargs):
            nonlocal calls
            calls += 1
            saved = original_save(name, content, **kwargs)
            if calls == 2:
                raise OSError("injected after writing")
            return saved

        with self.captureOnCommitCallbacks(execute=True):
            with patch.object(storage, "save", side_effect=fail_second):
                with self.assertRaises(OSError):
                    self.client.patch(self.detail(moment), {"caption": "No", "replace_photos": True,
                        "photos": [self.photo(), self.photo()]}, format="multipart")
        self.assertEqual(CoupleMomentPhotoModel.objects.count(), 1)
        self.assertEqual(CoupleMomentModel.objects.get().caption, "Coffee together")
        self.assertEqual(len(list(Path(self.media.name).rglob("*.png"))), 1)

    def test_delete_queue_cascade_and_cleanup_retention(self):
        moment = self.create(photos=[self.photo()])
        self.note(moment)
        self.client.force_authenticate(self.creator)
        storage = CoupleMomentPhotoModel._meta.get_field("image").storage
        with patch.object(storage, "delete", side_effect=OSError("offline")):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.delete(self.detail(moment)).status_code, 200)
        self.assertEqual(MomentFileDeletion.objects.count(), 1)
        self.assertFalse(CoupleMomentNoteModel.objects.exists())
        call_command("cleanup_moments", stdout=StringIO())
        self.assertEqual(MomentFileDeletion.objects.count(), 0)
        self.assertFalse(list(Path(self.media.name).rglob("*.png")))
        old = self.create()
        recent = self.create()
        CoupleMomentModel.objects.filter(pk=old["id"]).update(expires_at=timezone.now() - timedelta(days=31))
        CoupleMomentModel.objects.filter(pk=recent["id"]).update(expires_at=timezone.now() - timedelta(days=1))
        call_command("cleanup_moments", stdout=StringIO())
        self.assertFalse(CoupleMomentModel.objects.filter(pk=old["id"]).exists())
        self.assertTrue(CoupleMomentModel.objects.filter(pk=recent["id"]).exists())

    def test_account_and_couple_deletion_clean_related_content(self):
        for delete_couple in [False, True]:
            moment = self.create(photos=[self.photo()])
            self.note(moment)
            with self.captureOnCommitCallbacks(execute=True):
                if delete_couple:
                    self.couple.delete()
                else:
                    self.creator.delete()
            self.assertFalse(CoupleMomentModel.objects.exists())
            self.assertFalse(CoupleMomentNoteModel.objects.exists())
            self.assertFalse(list(Path(self.media.name).rglob("*.png")))
            if not delete_couple:
                # Restore a separate active pair for the second cascade scenario.
                self.creator = UserModel.objects.create_user(phone_number="+9779809999999")
                self.connection.sender_number = self.creator.phone_number
                self.connection.save()
                CoupleMembershipModel.objects.create(couple=self.couple, user=self.creator, position=1)
                self.client.force_authenticate(self.creator)

    def test_orphan_sweep_keeps_referenced_and_recent_files(self):
        import os
        self.create(photos=[self.photo()])
        storage = CoupleMomentPhotoModel._meta.get_field("image").storage
        old = storage.save("couples/moments/old.png", self.photo())
        recent = storage.save("couples/moments/recent.png", self.photo())
        referenced = CoupleMomentPhotoModel.objects.get().image.name
        old_time = (timezone.now() - timedelta(days=2)).timestamp()
        for name in [old, referenced]:
            os.utime(storage.path(name), (old_time, old_time))
        with self.captureOnCommitCallbacks(execute=True):
            call_command("cleanup_moments", sweep_orphans=True, stdout=StringIO())
        self.assertFalse(storage.exists(old))
        self.assertTrue(storage.exists(recent))
        self.assertTrue(storage.exists(referenced))

    def test_photo_authentication_and_direct_media_denial(self):
        moment = self.create(visibility="private", photos=[self.photo()])
        url = moment["photos"][0]["image"]
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)
        name = CoupleMomentPhotoModel.objects.get().image.name
        # Exercise the development media handler directly: Django's test runner
        # disables DEBUG, so static() routes may not be registered during discovery.
        from django.test import RequestFactory
        from srisu.urls import serve_public_media
        for path in [name, "other/../" + name, name.replace("couples/", "COUPLES/"),
                     name.replace("couples/", "couples./")]:
            response = serve_public_media(RequestFactory().get("/media/" + path), path,
                                          document_root=self.media.name)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/media/" + name).status_code, 404)
        self.assertEqual(self.client.get("/media/other/../" + name).status_code, 404)
        self.assertEqual(self.client.get("/media/" + name.replace("couples/", "COUPLES/")).status_code, 404)
        self.assertEqual(self.client.get("/media/" + name.replace("couples/", "couples./")).status_code, 404)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_feed_query_count_does_not_grow_with_results(self):
        self.create(photos=[self.photo()])
        with CaptureQueriesContext(connection) as first:
            self.client.get(self.url)
        for _ in range(4):
            self.create(photos=[self.photo()])
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(first), len(many))


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections
from django.test import skipUnlessDBFeature
from rest_framework.test import APITransactionTestCase, APIClient


class MomentConcurrencyTests(APITransactionTestCase):
    setUp = MomentAPITests.setUp
    photo = staticmethod(MomentAPITests.photo)
    create = MomentAPITests.create
    detail = MomentAPITests.detail

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_detail_requests_record_one_view_per_user(self):
        from social.models import CoupleMomentViewModel
        moment = self.create()
        barrier = Barrier(2)

        def view():
            try:
                client = APIClient()
                client.force_authenticate(self.sender)
                barrier.wait(timeout=10)
                return client.get(self.detail(moment))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(view) for _ in range(2)]
            for future in futures:
                response = future.result(timeout=20)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["total_view_count"], 1)
        self.assertEqual(CoupleMomentViewModel.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_appends_cannot_exceed_five_photos(self):
        moment = self.create(photos=[self.photo() for _ in range(4)])
        barrier = Barrier(2)

        def append():
            try:
                client = APIClient()
                client.force_authenticate(self.creator)
                barrier.wait(timeout=10)
                return client.patch(self.detail(moment), {"photos": [self.photo()]}, format="multipart").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append) for _ in range(2)]
            statuses = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(statuses), [200, 400])
        self.assertEqual(CoupleMomentPhotoModel.objects.count(), 5)
        self.assertEqual(len(list(Path(self.media.name).rglob("*.png"))), 5)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_delete_and_append_leave_no_uploads(self):
        moment = self.create(photos=[self.photo()])
        barrier = Barrier(2)

        def mutate(deleting):
            try:
                client = APIClient()
                client.force_authenticate(self.creator)
                barrier.wait(timeout=10)
                if deleting:
                    return client.delete(self.detail(moment)).status_code
                return client.patch(self.detail(moment), {"photos": [self.photo()]}, format="multipart").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            deleting = pool.submit(mutate, True)
            updating = pool.submit(mutate, False)
            self.assertEqual(deleting.result(timeout=20), 200)
            self.assertIn(updating.result(timeout=20), [200, 404])
        self.assertFalse(CoupleMomentModel.objects.exists())
        self.assertFalse(list(Path(self.media.name).rglob("*.png")))


class MomentMigrationTests(APITransactionTestCase):
    def test_existing_moments_backfill_expiry_and_audience(self):
        from django.db.migrations.executor import MigrationExecutor
        old_target = [("social", "0007_couple_profile_memberships")]
        executor = MigrationExecutor(connection)
        # Restore the current schema, including migrations added after the expiry work.
        new_target = executor.loader.graph.leaf_nodes()
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            User = apps.get_model("authentication", "UserModel")
            Couple = apps.get_model("social", "CoupleModel")
            Membership = apps.get_model("social", "CoupleMembershipModel")
            Moment = apps.get_model("social", "CoupleMomentModel")
            couple = Couple.objects.create()
            users = [User.objects.create(phone_number=f"+12345{i}") for i in range(2)]
            members = [Membership.objects.create(user=user, couple=couple, position=index + 1) for index, user in enumerate(users)]
            moment = Moment.objects.create(couple=couple, created_by=users[0], caption="Legacy", moment_date="2026-01-01")
            old_time = timezone.now() - timedelta(days=5)
            Moment.objects.filter(pk=moment.pk).update(created_at=old_time)
        finally:
            MigrationExecutor(connection).migrate(new_target)
        migrated = CoupleMomentModel.objects.get(pk=moment.pk)
        self.assertEqual(migrated.created_at, old_time)
        self.assertEqual(migrated.expires_at, old_time + timedelta(hours=24))
        self.assertEqual(migrated.audience_membership_ids, [m.pk for m in members])
        self.assertEqual(migrated.audience_user_ids, [u.pk for u in users])
