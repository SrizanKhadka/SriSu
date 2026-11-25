from uuid import uuid4
from unittest.mock import patch
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from authentication.models import UserModel
from social.models import CoupleConnectionModel, CoupleChallenge, ChallengeTemplate, ChallengeOperation, SingleConnectionModel, CoupleMembershipModel
from social.services.couple_profile_service import create_or_get_couple_for_connection
from social.services import challenges


class ChallengeTests(APITestCase):
    def setUp(self):
        cache.clear()
        import json
        from pathlib import Path
        catalogue = json.loads((Path(__file__).resolve().parents[1] / "contracts/challenges-1/catalogue.json").read_text())
        for order, item in enumerate(a for group in catalogue for a in group["activities"]):
            ChallengeTemplate.objects.get_or_create(pk=item["id"], defaults={"collection": item["collection"], "content": item, "order": order})
        self.one, self.two, self.other = [UserModel.objects.create_user(phone_number=f"+97798111100{i:02}", full_name=f"Challenge user {i}") for i in range(3)]
        self.connection = CoupleConnectionModel.objects.create(sender_number=self.one.phone_number, receiver_number=self.two.phone_number, connection_status="ACCEPTED")
        self.couple = create_or_get_couple_for_connection(self.connection)
        self.client.force_authenticate(self.one)

    def start(self, key=None, template="new-1-v1", status=201):
        response = self.client.post("/api/social/challenges/", {"request_id": str(key or uuid4()), "couple": self.couple.pk, "template_id": template}, format="json")
        self.assertEqual(response.status_code, status, getattr(response, "data", None))
        return getattr(response, "data", {}).get("data", {})

    def act(self, challenge, action, user=None, key=None, status=200):
        if user: self.client.force_authenticate(user)
        response = self.client.post(f"/api/social/challenges/{challenge['id']}/actions/", {
            "request_id": str(key or uuid4()), "action": action, "expected_revision": challenge["revision"]}, format="json")
        self.assertEqual(response.status_code, status, getattr(response, "data", None))
        return getattr(response, "data", {}).get("data", {})

    def test_full_flow_both_accept_pause_independently_and_confirm(self):
        c = self.start()
        self.assertEqual(c["accepted_by"], [self.one.pk])
        self.act(c, "cancel", self.two, status=409)
        self.client.force_authenticate(self.one)
        self.act(c, "accept", status=409)
        self.act(c, "confirm", self.two, status=409)
        c = self.act(c, "accept")
        self.assertEqual(c["state"], "active")
        c = self.act(c, "pause", self.one)
        c = self.act(c, "pause", self.two)
        c = self.act(c, "resume", self.one)
        self.assertEqual(c["state"], "paused")
        self.assertEqual(c["paused_by"], [self.two.pk])
        self.act(c, "confirm", status=409)
        c = self.act(c, "resume", self.two)
        c = self.act(c, "confirm")
        self.assertEqual(c["confirmed_by"], [self.two.pk])
        c = self.act(c, "unconfirm")
        self.assertEqual(c["confirmed_by"], [])
        c = self.act(c, "confirm", self.one)
        c = self.act(c, "confirm", self.two)
        self.assertEqual(c["state"], "completed")
        self.assertIsNotNone(c["completed_at"])
        self.act(c, "cancel", status=409)
        self.act(c, "unconfirm", status=409)
        history = self.client.get("/api/social/challenges/history/")
        self.assertEqual(history.data["data"]["items"][0]["id"], c["id"])
        self.assertIn("no-store", history["Cache-Control"])
        self.assertIsNone(self.client.get("/api/social/challenges/home/").data["data"]["current"])

    def test_retry_does_not_reapply_old_pause_or_duplicate_completion(self):
        key = uuid4(); c = self.start(key)
        self.assertEqual(self.start(key, status=200)["id"], c["id"])
        self.start(key, template="play-1-v1", status=409)
        self.start(status=409)
        c = self.act(c, "accept", self.two)
        before_pause = c; pause_key = uuid4()
        c = self.act(c, "pause", self.one, pause_key)
        c = self.act(c, "resume")
        retried = self.act(before_pause, "pause", key=pause_key)
        self.assertEqual(retried["state"], "active")
        self.assertEqual(retried["revision"], c["revision"])
        self.act(before_pause, "resume", key=pause_key, status=409)
        c = self.act(c, "confirm")
        before_complete = c; complete_key = uuid4()
        complete = self.act(c, "confirm", self.two, complete_key)
        retry = self.act(before_complete, "confirm", key=complete_key)
        self.assertEqual(retry["completed_at"], complete["completed_at"])
        self.assertEqual(retry["revision"], complete["revision"])

    def test_new_couple_chat_reconciliation_is_repeatable(self):
        from social.services.relationship_service import reconcile_accepted_relationship
        room = reconcile_accepted_relationship(self.couple.pk)
        self.assertEqual(room.pk, reconcile_accepted_relationship(self.couple.pk).pk)

    def test_decline_cancel_and_stale_revision_are_distinct(self):
        c = self.start(); self.act(c, "decline", status=409)
        declined = self.act(c, "decline", self.two)
        self.assertEqual(declined["state"], "declined")
        self.act(declined, "accept", status=409)
        c = self.start()
        active = self.act(c, "accept", self.one)
        self.act(c, "cancel", status=409)
        self.assertEqual(self.act(active, "cancel")["state"], "cancelled")

    def test_original_audience_only_and_session_expiry(self):
        c = self.start()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/social/challenges/{c['id']}/").status_code, 404)
        self.act(c, "accept", status=404); self.start(status=404)
        self.assertEqual(self.client.get("/api/social/challenges/history/").status_code, 404)
        self.client.force_authenticate(self.one)
        membership = self.couple.memberships.get(user=self.two)
        membership.ended_at = timezone.now(); membership.save()
        self.assertEqual(self.client.get(f"/api/social/challenges/{c['id']}/").status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/social/challenges/home/").status_code, 401)

    def test_catalogue_snapshot_withdrawal_and_history_pagination(self):
        home = self.client.get("/api/social/challenges/home/").data["data"]
        self.assertEqual([v["id"] for v in home["collections"]], ["gestures", "time", "play", "new"])
        self.assertEqual(sum(len(v["activities"]) for v in home["collections"]), 16)
        c = self.start(); original = c["activity"]
        ChallengeTemplate.objects.filter(pk="new-1-v1").update(published=False, content={"title": "Changed"})
        c = self.act(c, "accept", self.two)
        self.assertEqual(c["activity"], original)
        c = self.act(c, "cancel")
        self.start(status=409)
        instance = CoupleChallenge.objects.get(pk=c["id"])
        for _ in range(24):
            instance.pk = uuid4(); instance.save(force_insert=True)
        first = self.client.get("/api/social/challenges/history/").data["data"]
        second = self.client.get("/api/social/challenges/history/", {"before": first["next"]}).data["data"]
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len({c["id"] for c in first["items"] + second["items"]}), 25)
        self.assertIsNone(second["next"])
        self.assertEqual(self.client.get("/api/social/challenges/history/?before=wrong").status_code, 400)

    def test_blocked_partners_cannot_read_or_mutate_challenges(self):
        c = self.start()
        SingleConnectionModel.objects.create(sender_number=self.two.phone_number,
            receiver_number=self.one.phone_number, connection_status="BLOCKED")
        for user in (self.one, self.two):
            self.client.force_authenticate(user)
            self.assertIsNone(self.client.get("/api/social/challenges/home/").data["data"]["couple"])
            self.assertEqual(self.client.get(f"/api/social/challenges/{c['id']}/").status_code, 404)
            self.assertEqual(self.client.get("/api/social/challenges/history/").status_code, 404)
            self.act(c, "accept", status=404)

    def test_recreated_membership_cannot_recover_an_old_activity(self):
        c = self.start()
        old = self.couple.memberships.get(user=self.two)
        # Synthetic maintenance-style replacement; normal linking keeps historical positions unique.
        old.delete()
        CoupleMembershipModel.objects.create(couple=self.couple,user=self.two,position=2,is_owner=True)
        self.assertEqual(self.client.get(f"/api/social/challenges/{c['id']}/").status_code, 404)
        self.assertIsNone(self.client.get("/api/social/challenges/home/").data["data"]["current"])
        replacement = self.start()
        self.assertNotEqual(c["id"], replacement["id"])
        self.assertEqual(CoupleChallenge.objects.get(pk=c["id"]).state, "cancelled")
        self.assertEqual(self.client.get("/api/social/challenges/history/").data["data"]["items"], [])

    def test_strict_inputs_card_ownership_and_failed_transition_rollback(self):
        c = self.start()
        url = f"/api/social/challenges/{c['id']}/actions/"
        self.assertEqual(self.client.post(url, {"request_id": str(uuid4()), "action": "accept", "expected_revision": 1, "user": self.two.pk}, format="json").status_code, 400)
        self.client.force_authenticate(self.two)
        self.assertEqual(self.client.put(f"/api/social/challenges/{c['id']}/card/", {"event_id": "$test"}, format="json").status_code, 404)
        with patch.object(ChallengeOperation.objects, "create", side_effect=RuntimeError("synthetic failure")):
            self.client.raise_request_exception = False
            self.act(c, "accept", status=500)
        self.assertEqual(CoupleChallenge.objects.get(pk=c["id"]).state, "invited")
        self.act(c, "accept")

    def test_responses_match_contract(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/challenges-1/openapi.json").read_text())
        for route, name in [("home/", "HomeResponse"), ("history/", "HistoryResponse")]:
            Draft202012Validator(schema | {"$ref": "#/components/schemas/" + name}).validate(self.client.get("/api/social/challenges/" + route).json())
        c = self.start()
        Draft202012Validator(schema | {"$ref": "#/components/schemas/SessionResponse"}).validate(self.client.get(f"/api/social/challenges/{c['id']}/").json())


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL row locks")
class ChallengeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient(); ChallengeTests.setUp(self)
    def race(self, *actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try: return action()
                except challenges.ChallengeConflict: return "conflict"
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool: return list(pool.map(run, actions))
    def test_concurrent_invitations(self):
        result = self.race(*[lambda user=user: challenges.start(user, uuid4(), self.couple.pk, "new-1-v1") for user in [self.one, self.two]])
        self.assertEqual(result.count("conflict"), 1)
        self.assertEqual(CoupleChallenge.objects.filter(state="invited").count(), 1)
    def test_simultaneous_confirmation_preserves_first_and_retries_second(self):
        c, _ = challenges.start(self.one, uuid4(), self.couple.pk, "new-1-v1")
        c = challenges.act(self.two, c.pk, uuid4(), "accept", c.revision)
        result = self.race(*[lambda user=user: challenges.act(user, c.pk, uuid4(), "confirm", c.revision) for user in [self.one, self.two]])
        self.assertEqual(result.count("conflict"), 1)
        c.refresh_from_db(); self.assertEqual(len(c.confirmed_by), 1)
        remaining = self.one if self.one.pk not in c.confirmed_by else self.two
        c = challenges.act(remaining, c.pk, uuid4(), "confirm", c.revision)
        self.assertEqual(c.state, "completed"); self.assertEqual(c.revision, 4)
    def test_pause_racing_completion_never_completes_while_paused(self):
        c, _ = challenges.start(self.one, uuid4(), self.couple.pk, "new-1-v1")
        c = challenges.act(self.two, c.pk, uuid4(), "accept", c.revision)
        c = challenges.act(self.one, c.pk, uuid4(), "confirm", c.revision)
        self.race(lambda: challenges.act(self.one, c.pk, uuid4(), "pause", c.revision), lambda: challenges.act(self.two, c.pk, uuid4(), "confirm", c.revision))
        c.refresh_from_db()
        self.assertEqual(c.state, "paused" if c.paused_by else "completed")
