from uuid import uuid4
from unittest.mock import patch
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase
from authentication.models import UserModel
from social.models import CoupleConnectionModel, SparkSession, SparkAnswer
from social.services.couple_profile_service import create_or_get_couple_for_connection
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping


class SparkTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.one, self.two, self.other = [UserModel.objects.create_user(phone_number=f"+97798100000{i:02}", full_name=f"Spark user {i}") for i in range(3)]
        self.connection = CoupleConnectionModel.objects.create(sender_number=self.one.phone_number, receiver_number=self.two.phone_number, connection_status="ACCEPTED")
        self.couple = create_or_get_couple_for_connection(self.connection)
        self.room, _ = ChatRoom.objects.get_or_create(couple=self.couple, defaults={"user_one": self.one, "user_two": self.two})
        for user in [self.one, self.two]:
            MatrixUserMapping.objects.update_or_create(user=user, defaults={"state": "active", "matrix_localpart": f"spark{user.pk}", "matrix_user_id": f"@spark{user.pk}:test.invalid"})
        self.mapping, _ = MatrixRoomMapping.objects.update_or_create(chat_room=self.room, defaults={"state": "active", "room_alias_localpart": f"spark-{self.room.pk}", "matrix_room_id": "!spark:test.invalid", "member_matrix_localparts": [f"spark{u.pk}" for u in [self.one, self.two]]})
        self.client.force_authenticate(self.one)

    def start(self, key=None, question="deep-1-v1", status=201):
        result = self.client.post("/api/social/sparks/", {"request_id": str(key or uuid4()), "couple": self.couple.pk, "question_id": question}, format="json")
        self.assertEqual(result.status_code, status, result.data)
        return result.data.get("data", {})

    def answer(self, spark, marker="A", status=200):
        result = self.client.put(f"/api/social/sparks/{spark['id']}/answer/", {"envelope": self.envelope(marker)}, format="json")
        self.assertEqual(result.status_code, status, result.data)
        return result.data.get("data", {})

    @staticmethod
    def envelope(marker="A"):
        return {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": marker * 256, "session_id": "B" * 43}

    def get(self, spark):
        return self.client.get(f"/api/social/sparks/{spark['id']}/")

    def test_responses_match_versioned_contract(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator
        contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/sparks-1/openapi.json").read_text())
        for suffix, schema in [("home/", "HomeResponse")]:
            response = self.client.get("/api/social/sparks/" + suffix)
            Draft202012Validator(contract | {"$ref": "#/components/schemas/" + schema}).validate(response.json())
        spark = self.start()
        self.answer(spark)
        Draft202012Validator(contract | {"$ref": "#/components/schemas/SessionResponse"}).validate(self.get(spark).json())

    def test_recovery_history_is_paged_ids_only_and_audience_scoped(self):
        spark = self.start()
        self.answer(spark)
        self.client.force_authenticate(self.two)
        page = self.client.get("/api/social/sparks/history/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data["data"], {"ids": [spark["id"]], "next": None})
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(self.get(spark).data["data"]["answers"], [])
        template = SparkSession.objects.get(pk=spark["id"])
        for _ in range(31):
            template.pk = uuid4()
            template.state = "revealed"
            template.save(force_insert=True)
        first = self.client.get("/api/social/sparks/history/").data["data"]
        second = self.client.get("/api/social/sparks/history/", {"after": first["next"]}).data["data"]
        self.assertEqual(len(first["ids"]), 25)
        self.assertEqual(len(set(first["ids"] + second["ids"])), 32)
        self.assertIsNone(second["next"])
        self.assertEqual(self.client.get("/api/social/sparks/history/?after=invalid").status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/social/sparks/history/").status_code, 404)
        self.client.force_authenticate(self.one)
        self.mapping.membership_epoch += 1
        self.mapping.save()
        self.assertEqual(self.client.get("/api/social/sparks/history/").data["data"]["ids"], [])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/social/sparks/history/").status_code, 401)

    def test_four_decks_and_no_plaintext_contract(self):
        data = self.client.get("/api/social/sparks/home/").data["data"]
        self.assertEqual([d["id"] for d in data["decks"]], ["deep", "memories", "playful", "future"])
        self.assertTrue(all(len(d["questions"]) >= 4 for d in data["decks"]))
        spark = self.start()
        for bad in [{"answer": "private text"}, {"envelope": {"algorithm": "none", "ciphertext": "private text", "session_id": "B" * 43}}, {"envelope": self.envelope() | {"answer": "private text"}}]:
            self.assertEqual(self.client.put(f"/api/social/sparks/{spark['id']}/answer/", bad, format="json").status_code, 400)
        self.assertEqual(SparkAnswer.objects.count(), 0)

    def test_mutual_reveal_omits_partner_ciphertext_until_second_submission(self):
        spark = self.start()
        own = self.answer(spark)
        self.assertEqual(own["state"], "open")
        self.assertEqual(len(own["answers"]), 1)
        self.client.force_authenticate(self.two)
        waiting = self.get(spark)
        self.assertEqual(waiting.data["data"]["answers"], [])
        self.assertEqual(waiting.data["data"]["submitted_by"], [self.one.pk])
        self.assertIn("no-store", waiting["Cache-Control"])
        home = self.client.get("/api/social/sparks/home/").data["data"]
        self.assertEqual(home["active"]["answers"], [])
        revealed = self.answer(spark, "C")
        self.assertEqual(revealed["state"], "revealed")
        self.assertEqual(len(revealed["answers"]), 2)
        self.client.force_authenticate(self.one)
        self.assertEqual(self.get(spark).data["data"]["answers"], revealed["answers"])

    def test_recipient_can_answer_first_and_retries_are_exact(self):
        key = uuid4()
        spark = self.start(key)
        self.assertEqual(self.start(key, status=200)["id"], spark["id"])
        self.start(key, question="future-1-v1", status=409)
        self.start(status=409)
        self.client.force_authenticate(self.two)
        first = self.answer(spark)
        self.assertEqual(self.answer(spark)["revision"], first["revision"])
        self.answer(spark, "C", status=409)
        self.client.force_authenticate(self.one)
        self.assertEqual(self.answer(spark, "C")["state"], "revealed")
        self.answer(spark, "C")
        self.assertEqual(SparkAnswer.objects.count(), 2)
        self.start()

    def test_skip_deletes_both_pending_envelopes_and_is_idempotent(self):
        spark = self.start()
        self.answer(spark)
        self.client.force_authenticate(self.two)
        url = f"/api/social/sparks/{spark['id']}/skip/"
        for _ in range(2):
            result = self.client.post(url, {}, format="json")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data["data"]["answers"], [])
        self.assertEqual(SparkAnswer.objects.count(), 0)
        self.answer(spark, status=409)
        self.start()

    def test_revealed_cannot_be_skipped(self):
        spark = self.start()
        self.answer(spark)
        self.client.force_authenticate(self.two)
        self.answer(spark, "C")
        self.assertEqual(self.client.post(f"/api/social/sparks/{spark['id']}/skip/", {}, format="json").status_code, 409)
        self.assertEqual(SparkAnswer.objects.count(), 2)

    def test_unrelated_and_expired_session_cannot_read_or_mutate(self):
        spark = self.start()
        self.answer(spark)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get(spark).status_code, 404)
        self.answer(spark, status=404)
        self.start(status=404)
        self.assertEqual(self.client.post(f"/api/social/sparks/{spark['id']}/skip/", {}, format="json").status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.get(spark).status_code, 401)

    def test_relationship_and_matrix_epoch_changes_revoke_access(self):
        spark = self.start()
        self.answer(spark)
        self.mapping.membership_epoch += 1
        self.mapping.save(update_fields=["membership_epoch"])
        self.assertEqual(self.get(spark).status_code, 404)
        self.assertIsNone(self.client.get("/api/social/sparks/home/").data["data"]["active"])
        self.start()
        self.assertEqual(SparkAnswer.objects.count(), 0)
        self.connection.connection_status = "BREAK-UP"
        self.connection.save(update_fields=["connection_status"])
        self.assertEqual(self.get(spark).status_code, 404)

    def test_failed_second_submission_rolls_back_reveal(self):
        spark = self.start()
        self.answer(spark)
        self.client.force_authenticate(self.two)
        with patch.object(SparkSession, "save", side_effect=RuntimeError("synthetic failure")):
            self.client.raise_request_exception = False
            response = self.client.put(f"/api/social/sparks/{spark['id']}/answer/", {"envelope": self.envelope("C")}, format="json")
            self.assertEqual(response.status_code, 500)
        self.assertEqual(SparkAnswer.objects.count(), 1)
        self.assertEqual(self.get(spark).data["data"]["state"], "open")
        self.answer(spark, "C")


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from social.services import sparks as spark_service


@skipUnless(connection.vendor == "postgresql", "Row-lock concurrency requires isolated PostgreSQL")
class SparkConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        SparkTests.setUp(self)

    def race(self, *operations):
        barrier = Barrier(len(operations))
        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return operation()
                except spark_service.SparkConflict:
                    return "conflict"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_concurrent_creates_leave_exactly_one_active_spark(self):
        result = self.race(*[lambda user=user: spark_service.start(user, uuid4(), self.couple.pk, "deep-1-v1") for user in [self.one, self.two]])
        self.assertEqual(result.count("conflict"), 1)
        self.assertEqual(SparkSession.objects.filter(state="open").count(), 1)

    def test_simultaneous_answers_reveal_once_without_losing_either(self):
        spark, _ = spark_service.start(self.one, uuid4(), self.couple.pk, "deep-1-v1")
        self.race(*[lambda user=user: spark_service.submit(user, spark.pk, SparkTests.envelope()) for user in [self.one, self.two]])
        spark.refresh_from_db()
        self.assertEqual(spark.state, "revealed")
        self.assertEqual(spark.revision, 3)
        self.assertEqual(spark.answers.count(), 2)

    def test_skip_racing_second_answer_has_one_consistent_outcome(self):
        spark, _ = spark_service.start(self.one, uuid4(), self.couple.pk, "deep-1-v1")
        spark_service.submit(self.one, spark.pk, SparkTests.envelope())
        self.race(lambda: spark_service.skip(self.one, spark.pk), lambda: spark_service.submit(self.two, spark.pk, SparkTests.envelope("C")))
        spark.refresh_from_db()
        self.assertIn(spark.state, ["revealed", "skipped"])
        self.assertEqual(spark.answers.count(), 2 if spark.state == "revealed" else 0)
