from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APITestCase, APITransactionTestCase, APIClient
from django.utils import timezone
from .test_protocol import ProtocolFixtures, ProtocolRaceTests
from .models import SparkAnswer, Spark, Change


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class SparkTests(ProtocolFixtures, APITestCase):
    def start(self):
        message=self.envelope();self.send(message)
        path=self.path+f"sparks/{message['operation_id']}/"
        response=self.first_client.post(path,{"device_id":self.first_keys["device_id"]},format="json")
        self.assertEqual(response.status_code,200)
        return message["operation_id"],path

    def test_mutual_release_idempotency_no_history_ciphertext_and_third_user(self):
        target,path=self.start()
        answer=self.envelope(kind="card.response",target=target,version=1)
        first=self.first_client.post(path+"answer/",answer,format="json")
        self.assertEqual(first.status_code,200);self.assertEqual(first.data["data"]["answers"],[])
        self.assert_contract("spark",first.data["data"])
        self.assertFalse(first.data["data"]["revealed"])
        waiting=self.second_client.get(path,{"device_id":self.second_keys["device_id"]})
        self.assertEqual(waiting.status_code,200);self.assertEqual(waiting.data["data"]["answers"],[])
        self.assertNotIn(answer["operation_id"],str(self.sync(client=self.second_client,device=self.second_keys["device_id"]).data))
        self.assertEqual(self.third_client.get(path,{"device_id":self.second_keys["device_id"]}).status_code,404)
        self.assertEqual(self.first_client.post(path+"answer/",answer,format="json").status_code,200)
        changed={**answer,"operation_id":self.envelope()["operation_id"]}
        self.assertEqual(self.first_client.post(path+"answer/",changed,format="json").status_code,409)
        second=self.envelope(second=True,kind="card.response",target=target,version=1)
        revealed=self.second_client.post(path+"answer/",second,format="json")
        self.assertEqual(revealed.status_code,200);self.assertTrue(revealed.data["data"]["revealed"])
        self.assert_contract("spark",revealed.data["data"])
        self.assertEqual(len(revealed.data["data"]["answers"]),2);self.assertEqual(SparkAnswer.objects.count(),2)
        self.assertTrue(self.first_client.get(path,{"device_id":self.first_keys["device_id"]}).data["data"]["revealed"])
        self.send(self.envelope(kind="delete",target=target))
        self.assertFalse(SparkAnswer.objects.exclude(envelope={}).exists())
        self.assertEqual(self.second_client.get(path,{"device_id":self.second_keys["device_id"]}).status_code,404)

    def test_gate_bound_to_author_room_and_original_devices(self):
        target,path=self.start()
        self.assertEqual(self.second_client.post(path,{"device_id":self.second_keys["device_id"]},format="json").status_code,404)
        wrong=self.envelope(kind="card.response",target=self.envelope()["target_id"])
        self.assertEqual(self.first_client.post(path+"answer/",wrong,format="json").status_code,409)
        self.room.revoked_at=timezone.now();self.room.save(update_fields=["revoked_at"])
        self.assertEqual(self.first_client.get(path,{"device_id":self.first_keys["device_id"]}).status_code,404)

    def test_only_two_unresolved_sparks_and_deleted_card_frees_slot(self):
        target,_=self.start();self.start()
        message=self.envelope();self.send(message)
        path=self.path+f"sparks/{message['operation_id']}/"
        self.assertEqual(self.first_client.post(path,{"device_id":self.first_keys["device_id"]},format="json").status_code,409)
        self.send(self.envelope(kind="delete",target=target))
        self.assertEqual(self.first_client.post(path,{"device_id":self.first_keys["device_id"]},format="json").status_code,200)


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class SparkRaceTests(ProtocolFixtures, APITransactionTestCase):
    start = SparkTests.start
    concurrent = ProtocolRaceTests.concurrent

    def submit(self, credentials, path, answer):
        client = APIClient()
        client.credentials(**credentials)
        return client.post(path + "answer/", answer, format="json")

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_partner_answers_reveal_exactly_once(self):
        target, path = self.start()
        bodies = [self.envelope(second=bool(i), kind="card.response", target=target, version=1) for i in range(2)]
        credentials = [self.first_client._credentials, self.second_client._credentials]
        responses = self.concurrent(lambda i: self.submit(credentials[i], path, bodies[i]))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(sorted(len(r.data["data"]["answers"]) for r in responses), [0, 2])
        self.assertEqual(SparkAnswer.objects.count(), 2)
        self.assertIsNotNone(Spark.objects.get(pk=target).revealed_at)
        self.assertEqual(Change.objects.filter(room=self.room, kind="spark.changed").count(), 3)

    @skipUnlessDBFeature("has_select_for_update")
    def test_duplicate_answer_race_never_opens_gate_or_duplicates_change(self):
        target, path = self.start()
        body = self.envelope(kind="card.response", target=target, version=1)
        responses = self.concurrent(lambda _: self.submit(self.first_client._credentials, path, body))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual([r.data["data"]["answers"] for r in responses], [[], []])
        self.assertEqual(SparkAnswer.objects.count(), 1)
        self.assertIsNone(Spark.objects.get(pk=target).revealed_at)
        self.assertEqual(Change.objects.filter(room=self.room, kind="spark.changed").count(), 2)
