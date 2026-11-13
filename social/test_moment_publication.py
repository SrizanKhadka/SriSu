from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase, APITransactionTestCase

from social.test_moments import MomentAPITests
from social.models import (CoupleMomentModel, CoupleMomentPhotoModel, CoupleMomentViewModel,
    CoupleMembershipModel, MomentDraft, MomentDraftPhoto, MomentReaction)
from social.services.moment_publication import upload_photo, publish


class PublicationTests(APITestCase):
    setUp = MomentAPITests.setUp
    photo = staticmethod(MomentAPITests.photo)

    def draft(self):
        key = str(uuid4())
        payload = {"request_id": key, "couple": self.couple.pk}
        first = self.client.post("/api/social/moment-drafts/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post("/api/social/moment-drafts/", payload, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["data"]["id"], second.data["data"]["id"])
        return first.data["data"]

    def upload(self, draft, photo_id=None):
        photo_id = photo_id or str(uuid4())
        response = self.client.put(f"/api/social/moment-drafts/{draft['id']}/photos/{photo_id}/",
            {"photo": self.photo()}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["data"]

    def publish(self, draft, **kwargs):
        payload = {"expected_revision": draft["revision"], "caption": "A quiet morning",
            "visibility": "private", "photo_ids": [p["id"] for p in draft["photos"]], **kwargs}
        response = self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["data"], payload

    def test_publication_starts_clock_only_after_media_ready_and_replays(self):
        draft = self.upload(self.draft())
        MomentDraft.objects.filter(pk=draft["id"]).update(created_at=timezone.now() - timedelta(days=2))
        before = timezone.now()
        moment, payload = self.publish(draft)
        obj = CoupleMomentModel.objects.get(pk=moment["id"])
        self.assertGreaterEqual(obj.created_at, before)
        self.assertEqual(obj.expires_at - obj.created_at, timedelta(hours=24))
        replay = self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", payload, format="json")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["data"]["id"], obj.pk)
        self.assertEqual(CoupleMomentModel.objects.count(), 1)
        self.assertFalse(MomentDraftPhoto.objects.exists())
        self.assertTrue(obj.photos.first().image.storage.exists(obj.photos.first().image.name))
        payload["caption"] = "Different intent"
        self.assertEqual(self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", payload, format="json").status_code, 409)

    def test_both_partners_publish_but_staging_is_owner_only(self):
        draft = self.upload(self.draft())
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.get(f"/api/social/moment-drafts/{draft['id']}/").status_code, 404)
        theirs, _ = self.publish(self.upload(self.draft()))
        self.assertEqual(theirs["created_by"], self.partner.pk)
        self.client.force_authenticate(self.creator)
        mine, _ = self.publish(draft)
        self.assertEqual(mine["created_by"], self.creator.pk)

    def test_limits_owned_ids_order_and_upload_retry(self):
        draft = self.draft()
        key = str(uuid4())
        draft = self.upload(draft, key)
        replay = self.upload(draft, key)
        self.assertEqual(replay["revision"], draft["revision"])
        for _ in range(4):
            draft = self.upload(draft)
        response = self.client.put(f"/api/social/moment-drafts/{draft['id']}/photos/{uuid4()}/", {"photo": self.photo()}, format="multipart")
        self.assertEqual(response.status_code, 400)
        bad = {"expected_revision": draft["revision"], "photo_ids": [str(uuid4())]}
        self.assertEqual(self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", bad, format="json").status_code, 400)
        moment, _ = self.publish(draft, photo_ids=[p["id"] for p in reversed(draft["photos"])])
        self.assertEqual([p["order"] for p in moment["photos"]], list(range(5)))

    def test_failed_publish_retains_draft_and_retries(self):
        draft = self.upload(self.draft())
        with patch("social.services.moment_publication.CoupleMomentPhotoModel.objects.bulk_create", side_effect=RuntimeError("synthetic")):
            self.client.raise_request_exception = False
            response = self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", {
                "expected_revision": draft["revision"], "photo_ids": [draft["photos"][0]["id"]]}, format="json")
            self.assertEqual(response.status_code, 500)
        self.assertEqual(CoupleMomentModel.objects.count(), 0)
        self.assertEqual(MomentDraftPhoto.objects.count(), 1)
        self.assertIsNone(MomentDraft.objects.get(pk=draft["id"]).published_at)
        self.publish(draft)

    def test_views_prefetch_reactions_creator_edits_and_expiry(self):
        moment, payload = self.publish(self.upload(self.draft()))
        base = f"/api/social/couple-moments/{moment['id']}/"
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.get(base + "?record_view=false").status_code, 200)
        self.client.get(f"/api/social/couple-moments/sequence/?couple={self.couple.pk}&mode=all")
        self.assertEqual(CoupleMomentViewModel.objects.count(), 0)
        for _ in range(2):
            self.assertEqual(self.client.post(base + "viewed/", {}, format="json").status_code, 200)
            self.assertEqual(self.client.put(base + "reaction/", {"reacted": True}, format="json").status_code, 200)
        self.assertEqual(CoupleMomentViewModel.objects.count(), 1)
        self.assertEqual(MomentReaction.objects.count(), 1)
        self.assertEqual(self.client.patch(base + "caption/", {"caption": "No", "expected_revision": 1}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(base).status_code, 403)
        self.client.force_authenticate(self.creator)
        data = self.client.get(base + "?record_view=false").data
        self.assertIsNotNone(data["partner_seen_at"])
        self.assertTrue(data["partner_reacted"])
        changed = self.client.patch(base + "caption/", {"caption": "Updated", "expected_revision": 1}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data["data"]["expires_at"], moment["expires_at"])
        self.assertEqual(self.client.patch(base + "caption/", {"caption": "Stale", "expected_revision": 1}, format="json").status_code, 409)
        expires = CoupleMomentModel.objects.get(pk=moment["id"]).expires_at
        with patch("django.utils.timezone.now", return_value=expires):
            self.assertEqual(self.client.get(base + "?record_view=false").status_code, 410)
            self.assertEqual(self.client.get(moment["photos"][0]["image"]).status_code, 404)
            self.assertEqual(self.client.post(base + "viewed/", {}, format="json").status_code, 404)
            self.assertEqual(self.client.get("/api/social/couple-moments/home/").data["data"]["couple"]["members"][0]["active_count"], 0)

    def test_private_audience_and_public_visitor_note_boundary(self):
        private, _ = self.publish(self.upload(self.draft()))
        public, _ = self.publish(self.upload(self.draft()), visibility="public")
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.get(f"/api/social/couple-moments/{private['id']}/?record_view=false").status_code, 404)
        base = f"/api/social/couple-moments/{public['id']}/"
        data = self.client.get(base + "?record_view=false").data
        self.assertFalse(data["can_reply"])
        self.assertFalse(data["can_edit"])
        self.assertIsNone(data["partner_seen_at"])
        self.assertEqual(self.client.put(base + "reaction/", {"reacted": True}, format="json").status_code, 403)
        self.assertEqual(self.client.post(base + "notes/", {"message": "Lovely view"}, format="json").status_code, 201)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(base).status_code, 401)

    def test_relinked_membership_history_does_not_grant_access(self):
        draft = self.upload(self.draft())
        moment, _ = self.publish(draft)
        old = CoupleMembershipModel.objects.get(couple=self.couple, user=self.partner)
        old.ended_at = timezone.now()
        old.save(update_fields=["ended_at"])
        self.connection.receiver_number = self.new_partner.phone_number
        self.connection.save()
        # A corrupt/imported extra position must not make ended audience rows eligible.
        CoupleMembershipModel.objects.create(couple=self.couple, user=self.new_partner, position=3)
        self.client.force_authenticate(self.new_partner)
        self.assertEqual(self.client.get(f"/api/social/couple-moments/{moment['id']}/").status_code, 404)
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(f"/api/social/moment-drafts/{draft['id']}/").status_code, 404)

    def test_deletion_replay_never_resurrects_publication(self):
        draft = self.upload(self.draft())
        moment, body = self.publish(draft)
        self.client.delete(f"/api/social/couple-moments/{moment['id']}/")
        self.assertEqual(self.client.post(f"/api/social/moment-drafts/{draft['id']}/publish/", body, format="json").status_code, 410)
        self.assertEqual(CoupleMomentModel.objects.count(), 0)

    def test_image_metadata_and_storage_outside_transaction(self):
        draft = self.draft()
        # APITestCase has its own outer transaction; the service must add none during storage.
        storage = MomentDraftPhoto._meta.get_field("image").storage
        original = storage.save
        depths = []
        def save(*args, **kwargs):
            depths.append(len(connection.atomic_blocks))
            return original(*args, **kwargs)
        initial_depth = len(connection.atomic_blocks)
        with patch.object(storage, "save", side_effect=save):
            self.upload(draft)
        self.assertEqual(depths, [initial_depth])
        photo = MomentDraftPhoto.objects.get()
        with photo.image.open("rb") as file:
            image = Image.open(file)
            self.assertEqual(image.format, "JPEG")
            self.assertFalse(image.getexif())
            self.assertEqual(image.size, (photo.width, photo.height))

    def test_live_projection_matches_shared_contract(self):
        from tools.check_core_contracts import validate_contract
        draft = self.upload(self.draft())
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        validate_contract("momentDraft", json.loads(json.dumps({"data": draft}, cls=DjangoJSONEncoder)))
        moment, _ = self.publish(draft)
        validate_contract("moment", moment)
        validate_contract("momentHome", self.client.get("/api/social/couple-moments/home/").json())
        self.client.force_authenticate(self.sender)
        home = self.client.get("/api/social/couple-moments/home/")
        self.assertEqual(home.status_code, 200)
        self.assertIsNone(home.data["data"]["couple"])

    def test_malformed_oversized_and_animated_photos_are_rejected_without_rows(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        draft = self.draft()
        url = f"/api/social/moment-drafts/{draft['id']}/photos/{uuid4()}/"
        invalid = [SimpleUploadedFile("fake.jpg", b"not an image", content_type="image/jpeg"),
            SimpleUploadedFile("large.jpg", b"x" * (10 * 1024 * 1024 + 1), content_type="image/jpeg")]
        gif = BytesIO()
        Image.new("RGB", (2, 2), "red").save(gif, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100, loop=0)
        invalid.append(SimpleUploadedFile("fake.jpg", gif.getvalue(), content_type="image/jpeg"))
        for photo in invalid:
            self.assertEqual(self.client.put(url, {"photo": photo}, format="multipart").status_code, 400)
        self.assertFalse(MomentDraftPhoto.objects.exists())

    def test_author_cursor_ties_order_and_foreign_cursor(self):
        moments = [self.publish(self.upload(self.draft()))[0] for _ in range(3)]
        same = timezone.now()
        CoupleMomentModel.objects.update(created_at=same)
        url = f"/api/social/couple-moments/sequence/?couple={self.couple.pk}&creator={self.creator.pk}&mode=all&page_size=1"
        ids = []
        first = None
        while url:
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200)
            ids.extend(row["id"] for row in result.data["data"]["results"])
            url = result.data["data"]["next"]
            first = first or url
        self.assertEqual(ids, sorted(row["id"] for row in moments))
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.get(first).status_code, 400)

    def test_expired_draft_replay_and_discard_do_not_revive_files(self):
        draft = self.upload(self.draft())
        MomentDraft.objects.filter(pk=draft["id"]).update(created_at=timezone.now()-timedelta(days=7))
        self.assertEqual(self.client.get(f"/api/social/moment-drafts/{draft['id']}/").status_code, 410)
        from django.core.management import call_command
        call_command("cleanup_moments")
        self.assertFalse(MomentDraft.objects.filter(pk=draft["id"]).exists())



@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL row locks")
class PublicationConcurrencyTests(APITransactionTestCase):
    setUp = MomentAPITests.setUp
    photo = staticmethod(MomentAPITests.photo)

    def test_concurrent_uploads_are_bounded_and_publish_is_single(self):
        from social.services.moment_publication import create_draft
        from authentication.models import UserModel
        self.client = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient()
        draft, _ = create_draft(self.creator, uuid4(), self.couple.pk)
        for _ in range(4):
            upload_photo(self.creator, draft.id, uuid4(), self.photo())
        barrier = Barrier(2)
        def add(_):
            close_old_connections()
            try:
                user = UserModel.objects.get(pk=self.creator.pk)
                barrier.wait()
                upload_photo(user, draft.id, uuid4(), self.photo())
                return "ok"
            except Exception as error:
                return getattr(error, "status_code", type(error).__name__)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(add, range(2))), ["ok", 400])
        draft.refresh_from_db()
        self.assertEqual(draft.photos.count(), 5)
        data = {"expected_revision": draft.revision, "caption": "Together", "visibility": "private",
            "photo_ids": list(draft.photos.values_list("pk", flat=True))}
        def send(_):
            close_old_connections()
            try:
                barrier.wait()
                return publish(UserModel.objects.get(pk=self.creator.pk), draft.id, data)[0].pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(send, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(CoupleMomentModel.objects.count(), 1)
