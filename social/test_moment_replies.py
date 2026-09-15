from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from social.models import (CoupleMomentModel, CoupleMembershipModel, CoupleMomentNoteReplyModel,
                           CoupleMomentViewModel, SingleConnectionModel)
from social.test_moments import MomentAPITests as Fixtures


class MomentReplyAndViewTests(APITestCase):
    setUp = Fixtures.setUp
    create = Fixtures.create
    detail = Fixtures.detail
    note = Fixtures.note
    photo = staticmethod(Fixtures.photo)

    def prepare_note(self):
        moment = self.create()
        note = self.note(moment)
        return moment, note, reverse("moment_notes-replies", args=[note["id"]])

    def test_either_partner_can_reply_regardless_of_moment_creator(self):
        for creator in [self.creator, self.partner]:
            self.client.force_authenticate(creator)
            moment, note, url = self.prepare_note()
            for author in [self.creator, self.partner]:
                self.client.force_authenticate(author)
                response = self.client.post(url, {"message": "  Thank you!  ", "author": self.other.pk,
                    "note": 999}, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data["data"]["author"], author.pk)
                self.assertEqual(response.data["data"]["note"], note["id"])
                self.assertEqual(response.data["data"]["message"], "Thank you!")
            self.client.force_authenticate(self.sender)
            response = self.client.get(url)
            self.assertEqual(response.data["data"]["count"], 2)
            self.assertEqual([r["author"] for r in response.data["data"]["results"]],
                             [self.creator.pk, self.partner.pk])

    def test_reply_permissions_validation_and_throttle(self):
        moment, note, url = self.prepare_note()
        self.assertEqual(self.client.post(url, {"message": "Sender cannot reply"}, format="json").status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(url, {"message": "Stranger"}, format="json").status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(url, {"message": "Anonymous"}, format="json").status_code, 401)
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.head(url).status_code, 200)
        self.assertFalse(CoupleMomentNoteReplyModel.objects.exists())
        for message in ["", "  \n ", "x" * 1001]:
            self.assertEqual(self.client.post(url, {"message": message}, format="json").status_code, 400)
        from django.core.cache import cache
        cache.clear()
        with patch("social.api.moment_views.MomentReplyThrottle.rate", "1/hour"):
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url, {"message": "Thanks"}, format="json").status_code, 201)
            self.assertEqual(self.client.post(url, {"message": "Again"}, format="json").status_code, 429)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_embedded_notes_and_replies_are_only_for_couple_members(self):
        moment, note, url = self.prepare_note()
        self.client.force_authenticate(self.partner)
        self.client.post(url, {"message": "Thank you"}, format="json")
        for user in [self.creator, self.partner]:
            self.client.force_authenticate(user)
            detail = self.client.get(self.detail(moment)).data
            listed = self.client.get(self.url).data["data"]["results"][0]
            for data in [detail, listed]:
                self.assertEqual(data["appreciation_notes"][0]["id"], note["id"])
                self.assertEqual(data["appreciation_notes"][0]["replies"][0]["message"], "Thank you")
        for user in [self.sender, self.other]:
            self.client.force_authenticate(user)
            self.assertNotIn("appreciation_notes", self.client.get(self.detail(moment)).data)
            self.assertNotIn("appreciation_notes", self.client.get(self.url).data["data"]["results"][0])
        self.client.force_authenticate(self.sender)
        data = self.client.get(reverse("moment_notes-detail", args=[note["id"]])).data
        self.assertEqual(len(data["replies"]), 1)

    def test_blocking_and_membership_changes_protect_replies_and_embeds(self):
        moment, note, url = self.prepare_note()
        self.client.force_authenticate(self.partner)
        self.client.post(url, {"message": "Thanks"}, format="json")
        blocked = SingleConnectionModel.objects.create(sender_number=self.partner.phone_number,
            receiver_number=self.sender.phone_number, connection_status="BLOCKED")
        self.assertEqual(self.client.post(url, {"message": "Blocked"}, format="json").status_code, 404)
        self.assertEqual(self.client.get(self.detail(moment)).data["appreciation_notes"], [])
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.get(url).status_code, 404)
        blocked.delete()
        CoupleMembershipModel.objects.filter(user=self.partner).update(user=self.new_partner)
        self.connection.receiver_number = self.new_partner.phone_number
        self.connection.save()
        self.client.force_authenticate(self.new_partner)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"message": "Replacement"}, format="json").status_code, 404)
        self.client.force_authenticate(self.creator)
        self.connection.connection_status = "BREAK-UP"
        self.connection.save()
        self.assertEqual(self.client.post(url, {"message": "Inactive"}, format="json").status_code, 404)

    def test_replies_survive_expiry_but_cascade_with_note(self):
        moment, note, url = self.prepare_note()
        CoupleMomentModel.objects.filter(pk=moment["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.post(url, {"message": "Thanks for yesterday"}, format="json").status_code, 201)
        self.client.force_authenticate(self.sender)
        self.assertEqual(self.client.get(url).data["data"]["count"], 1)
        self.client.delete(reverse("moment_notes-detail", args=[note["id"]]))
        self.assertFalse(CoupleMomentNoteReplyModel.objects.exists())
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unique_views_only_count_successful_detail_gets(self):
        moment = self.create(photos=[self.photo()])
        self.assertEqual(moment["total_view_count"], 0)
        self.client.get(self.url)
        self.client.head(self.detail(moment))
        response = self.client.get(moment["photos"][0]["image"])
        b"".join(response.streaming_content)
        self.assertFalse(CoupleMomentViewModel.objects.exists())
        for user, expected in [(self.creator, 1), (self.creator, 1), (self.partner, 2), (self.sender, 3)]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.detail(moment)).data["total_view_count"], expected)
        self.assertEqual(self.client.get(self.url).data["data"]["results"][0]["total_view_count"], 3)
        self.client.force_authenticate(self.creator)
        response = self.client.patch(self.detail(moment), {"total_view_count": 999, "caption": "Updated"}, format="json")
        self.assertEqual(response.data["data"]["total_view_count"], 3)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 401)
        CoupleMomentModel.objects.filter(pk=moment["id"]).update(expires_at=timezone.now())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
        self.assertEqual(CoupleMomentViewModel.objects.count(), 3)

    def test_hidden_views_do_not_count_and_viewer_deletion_preserves_total(self):
        moment = self.create(visibility="private")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.detail(moment)).status_code, 404)
        self.assertEqual(CoupleMomentViewModel.objects.count(), 0)
        self.client.force_authenticate(self.creator)
        self.client.patch(self.detail(moment), {"visibility": "public"}, format="json")
        self.client.force_authenticate(self.other)
        self.client.get(self.detail(moment))
        self.other.delete()
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url).data["data"]["results"][0]["total_view_count"], 1)
        self.client.delete(self.detail(moment))
        self.assertFalse(CoupleMomentViewModel.objects.exists())

    def test_embedded_threads_do_not_add_queries_per_moment(self):
        def create_thread():
            self.client.force_authenticate(self.creator)
            moment, note, url = self.prepare_note()
            self.client.force_authenticate(self.partner)
            self.client.post(url, {"message": "Thanks"}, format="json")
            self.client.force_authenticate(self.creator)
        create_thread()
        with CaptureQueriesContext(connection) as first:
            self.client.get(self.url)
        for _ in range(3):
            create_thread()
        with CaptureQueriesContext(connection) as several:
            response = self.client.get(self.url)
        self.assertEqual(response.data["data"]["count"], 4)
        self.assertEqual(len(first), len(several))
