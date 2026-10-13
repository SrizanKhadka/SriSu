from datetime import timedelta
from uuid import uuid4
from unittest.mock import Mock, patch
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from couple_chat.test_protocol import ProtocolFixtures
from .models import Delivery, Event, Installation, Preferences
from .provider import ProviderFailure, message
from .services import destination
from .worker import run_once


@override_settings(NOTIFICATIONS_ENABLED=True)
class NotificationTests(ProtocolFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.registration = {"installation_id": str(uuid4()), "proof": "s" * 48, "version": 1,
            "platform": "android", "target_kind": "token", "target": "synthetic-provider-target-only", "permitted": True}
        self.bind()

    def bind(self, client=None, **changes):
        body = self.registration | changes
        return (client or self.second_client).put("/api/notifications/v1/installations/", body, format="json")

    def ready(self):
        Delivery.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))

    def test_duplicate_message_payload_and_tap_are_private(self):
        body = self.envelope()
        self.send(body); self.send(body)
        self.assertEqual(Event.objects.filter(kind="chat.message").count(), 1)
        row = Delivery.objects.get(event__kind="chat.message")
        wire = message(row, audible=True)
        self.assertEqual(set(wire.data), {"schema", "delivery_id", "expires_at"})
        self.assertNotIn(body["ciphertext"], str(wire.data))
        self.assertEqual(wire.notification.body, "You have an update. Open SriSu to view it.")
        path = f"/api/notifications/v1/deliveries/{row.pk}/"
        self.assertEqual(self.third_client.get(path).status_code, 404)
        self.assertEqual(self.first_client.get(path).status_code, 404)
        self.assertEqual(self.second_client.get(path).data["data"]["message_id"], body["target_id"])
        self.send(self.envelope(kind="delete", target=body["target_id"]))
        self.assertEqual(self.second_client.get(path).status_code, 404)
        self.ready(); sender=Mock(); run_once(sender)
        sender.assert_not_called()

    def test_binding_proof_version_and_transfer_fence_old_callbacks(self):
        self.send(self.envelope())
        self.assertEqual(self.bind(self.third_client, version=2, proof="x" * 48).status_code, 409)
        self.assertEqual(self.bind(self.third_client, version=2).status_code, 200)
        self.assertEqual(self.bind(version=1).status_code, 409)
        self.ready(); sender=Mock(); run_once(sender); sender.assert_not_called()
        self.assertEqual(Installation.objects.get().user_id, self.third.pk)

    def test_registered_fid_uses_fid_target_without_token(self):
        target="c"+"a"*21
        self.assertEqual(self.bind(version=2,target_kind="fid",target=target).status_code,200)
        self.send(self.envelope())
        wire=message(Delivery.objects.get(),audible=False)
        self.assertEqual(wire.fid,target)
        self.assertIsNone(wire.token)
        self.assertEqual(set(wire.data),{"schema","delivery_id","expires_at"})

    def test_invalid_provider_payload_does_not_retire_installation(self):
        from firebase_admin.exceptions import InvalidArgumentError
        self.send(self.envelope());self.ready()
        with patch("notifications.provider.app",return_value=object()), \
                patch("firebase_admin.messaging.send",side_effect=InvalidArgumentError("Synthetic invalid payload")):
            run_once()
        self.assertTrue(Installation.objects.get().active)
        self.assertEqual(Delivery.objects.get().outcome,"configuration")

    def test_cascade_after_claim_does_not_crash_worker(self):
        from notifications.worker import claim
        self.send(self.envelope());self.ready()
        def claim_then_delete():
            result=claim()
            Event.objects.all().delete()
            return result
        sender=Mock()
        with patch("notifications.worker.claim",side_effect=claim_then_delete):
            self.assertTrue(run_once(sender))
        sender.assert_not_called()

    def test_invalid_target_response_cannot_retire_rotated_binding(self):
        self.send(self.envelope()); self.ready()
        def rotate_then_fail(*args, **kwargs):
            self.assertEqual(self.bind(version=2, target="replacement-synthetic-target").status_code, 200)
            raise ProviderFailure("unregistered")
        run_once(rotate_then_fail)
        self.assertTrue(Installation.objects.get().active)

    def test_session_revoke_and_mute_suppress(self):
        self.send(self.envelope()); self.ready()
        pref=Preferences.objects.create(user=self.second, mute_forever=True)
        sender=Mock(); run_once(sender); sender.assert_not_called()
        pref.mute_forever=False;pref.save()
        self.send(self.envelope()); self.ready()
        from authentication.models import DeviceSession
        DeviceSession.objects.filter(pk=self.second_sid).update(revoked_at=timezone.now())
        run_once(sender); sender.assert_not_called()

    def test_retry_is_bounded_and_expired_taps_neutral(self):
        self.send(self.envelope()); self.ready()
        run_once(Mock(side_effect=ProviderFailure("transient", 120)))
        row=Delivery.objects.get(event__kind="chat.message")
        self.assertEqual(row.state,"pending")
        self.assertGreater(row.next_attempt_at,timezone.now()+timedelta(seconds=119))
        Event.objects.filter(pk=row.event_id).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.ready(); sender=Mock();run_once(sender);sender.assert_not_called()

    def test_dormant_installation_is_erased_and_cannot_get_history(self):
        from notifications.worker import cleanup
        self.send(self.envelope())
        Installation.objects.update(updated_at=timezone.now()-timedelta(days=31))
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()
        cleanup()
        install=Installation.objects.get()
        self.assertFalse(install.active)
        self.assertEqual(install.target, "")
        self.assertEqual(self.bind(version=2).status_code,200)
        self.assertFalse(Delivery.objects.filter(state="pending").exists())

    def test_transaction_rollback_and_no_history_fanout(self):
        from notifications.services import emit
        count=Event.objects.count()
        with self.assertRaises(ValueError):
            with transaction.atomic():
                emit("invite.accepted", self.second, "rollback", self.room.couple_id, room=self.room)
                raise ValueError()
        self.assertEqual(Event.objects.count(),count)
        Installation.objects.update(permitted=False)
        self.send(self.envelope())
        self.assertFalse(Delivery.objects.exists())
        self.bind(version=2)
        self.assertFalse(Delivery.objects.exists())

    def test_chat_coalescing_and_read_suppression(self):
        self.send(self.envelope()); latest=self.envelope();self.send(latest)
        self.assertEqual(Delivery.objects.filter(state="pending").count(),1)
        self.send(self.envelope(second=True,kind="receipt.read",target=latest["target_id"]),client=self.second_client)
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()

    def test_preferences_conflict_and_owner_isolation(self):
        path="/api/notifications/v1/preferences/"
        pref=self.second_client.get(path).data["data"]
        data={"expected_revision":pref["revision"],"push":False,"chat":True,"activity":True,"mute":"8h"}
        self.assertEqual(self.second_client.put(path,data,format="json").status_code,200)
        self.assertEqual(self.second_client.put(path,data,format="json").status_code,409)
        self.assertTrue(self.third_client.get(path).data["data"]["push"])

    def test_invalid_correlation_rolls_back_message(self):
        body=self.envelope(notification_plan_id=999999)
        self.send(body,status=400)
        self.assertFalse(Event.objects.filter(kind="chat.message").exists())
        from couple_chat.models import Message
        self.assertFalse(Message.objects.filter(pk=body["target_id"]).exists())

    def test_plan_and_first_card_have_only_one_alert(self):
        from social.models import CouplePlanModel
        from notifications.services import partner_event
        with transaction.atomic():
            plan=CouplePlanModel.objects.create(couple=self.room.couple,created_by=self.first,request_id=uuid4(),
                title="Synthetic plan",starts_at=timezone.now()+timedelta(hours=2),
                audience_membership_ids=sorted([self.room.first_membership_id,self.room.second_membership_id]))
            partner_event("plan.created",self.first,self.room.couple,plan.pk,plan.pk,expires_at=plan.starts_at)
        self.send(self.envelope(notification_plan_id=plan.pk))
        self.assertEqual(Delivery.objects.filter(state="pending").count(),1)
        self.assertFalse(Event.objects.filter(kind="chat.message").exists())
        # A later deliberate share is a new message, not silent forever.
        self.send(self.envelope(notification_plan_id=plan.pk))
        self.assertEqual(Event.objects.filter(kind="chat.message").count(),1)
        # Either current partner may deliberately share the same Plan.
        self.send(self.envelope(second=True,notification_plan_id=plan.pk),client=self.second_client)
        self.assertEqual(Event.objects.filter(kind="chat.message").count(),2)

    def test_meaningful_plan_response_and_sharing_proposal(self):
        path=f"/api/social/profiles/{self.room.couple_id}/"
        body={"request_id":str(uuid4()),"title":"Synthetic", "starts_at":(timezone.now()+timedelta(hours=2)).isoformat(),"share_to_legacy_chat":False}
        response=self.first_client.post(path+"plans/",body,format="json")
        self.assertEqual(response.status_code,201,response.data)
        plan=response.data["data"]
        self.assertEqual(Event.objects.filter(kind="plan.created").count(),1)
        self.first_client.post(path+"plans/",body,format="json")
        self.assertEqual(Event.objects.filter(kind="plan.created").count(),1)
        response_body={"expected_revision":plan["revision"],"response":"yes","response_note":""}
        result=self.second_client.patch(path+f"plans/{plan['id']}/",response_body,format="json")
        self.assertEqual(result.status_code,200,result.data)
        response_body["expected_revision"]=result.data["data"]["revision"]
        self.second_client.patch(path+f"plans/{plan['id']}/",response_body,format="json")
        self.assertEqual(Event.objects.filter(kind="plan.responded").count(),1)
        revision=self.first_client.get(path).data["data"]["revisions"]["sharing"]
        result=self.first_client.patch(path+"sections/sharing/",{"expected_revision":revision,"action":"propose","sections":["story"]},format="json")
        self.assertEqual(result.status_code,200,result.data)
        event=Event.objects.get(kind="sharing.proposed")
        self.assertEqual(destination(event,self.second,self.second_sid)["kind"],"sharing")
        revision=self.second_client.get(path).data["data"]["revisions"]["sharing"]
        self.second_client.patch(path+"sections/sharing/",{"expected_revision":revision,"action":"approve"},format="json")
        from rest_framework.exceptions import NotFound
        with self.assertRaises(NotFound):destination(event,self.second,self.second_sid)

    def test_spark_reveal_alerts_only_waiting_original_device(self):
        body=self.envelope();self.send(body);target=body["target_id"]
        path=self.path+f"sparks/{target}/"
        self.assertEqual(self.first_client.post(path,{"device_id":self.first_keys["device_id"]},format="json").status_code,200)
        answer=self.envelope(second=True,kind="card.response",target=target,version=1)
        self.second_client.post(path+"answer/",answer,format="json")
        self.assertFalse(Event.objects.filter(kind="spark.revealed").exists())
        other=self.envelope(kind="card.response",target=target,version=1)
        self.first_client.post(path+"answer/",other,format="json")
        self.first_client.post(path+"answer/",other,format="json")
        event=Event.objects.get(kind="spark.revealed")
        self.assertEqual(event.recipient_id,self.second.pk)
        self.assertEqual(str(event.device_id),self.second_keys["device_id"])

    def test_changed_shared_content_invalidates_proposal_notification(self):
        from social.models import CoupleStoryAnswerModel
        from rest_framework.exceptions import NotFound
        path=f"/api/social/profiles/{self.room.couple_id}/"
        revision=self.first_client.get(path).data["data"]["revisions"]["sharing"]
        result=self.first_client.patch(path+"sections/sharing/",
            {"expected_revision":revision,"action":"propose","sections":["story"]},format="json")
        self.assertEqual(result.status_code,200,result.data)
        event=Event.objects.get(kind="sharing.proposed")
        self.assertEqual(destination(event,self.second,self.second_sid)["kind"],"sharing")
        CoupleStoryAnswerModel.objects.create(couple=self.room.couple, author=self.first,
            prompt=CoupleStoryAnswerModel.PROMPTS[0][0], answer="Changed synthetic answer")
        with self.assertRaises(NotFound):
            destination(event,self.second,self.second_sid)
        self.ready();sender=Mock();run_once(sender);sender.assert_not_called()


from couple_chat.tests import Fixtures
@override_settings(NOTIFICATIONS_ENABLED=True)
class InvitationNotificationTests(Fixtures, APITestCase):
    def test_invitation_and_acceptance_are_once_and_terminal_state_cannot_resolve(self):
        from social.services.relationship_service import invite
        from rest_framework.exceptions import NotFound
        event=Event.objects.get(kind="invite.received")
        self.assertEqual(event.recipient_id,self.second.pk)
        self.assertEqual(destination(event,self.second,None),{"kind":"invitations"})
        invite(self.first,self.second.phone_number)
        self.assertEqual(Event.objects.count(),1)
        self.accept();self.accept()
        self.assertEqual(Event.objects.filter(kind="invite.accepted",recipient=self.first).count(),1)
        with self.assertRaises(NotFound):destination(event,self.second,None)


from django.test import skipUnlessDBFeature
from rest_framework.test import APITransactionTestCase
from couple_chat.test_protocol import ProtocolRaceTests
@override_settings(NOTIFICATIONS_ENABLED=True)
class NotificationRaceTests(ProtocolFixtures, APITransactionTestCase):
    concurrent = ProtocolRaceTests.concurrent
    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    def test_duplicate_producer_and_competing_workers(self):
        from .services import register
        from .worker import claim
        from rest_framework.test import APIClient
        register(self.second,self.second_sid,{"installation_id":uuid4(),"proof":"s"*48,"version":1,
            "platform":"android","target_kind":"token","target":"synthetic-race-target","permitted":True})
        body=self.envelope()
        credentials=self.first_client._credentials
        def submit(_):
            client=APIClient();client.credentials(**credentials)
            return client.post(self.path+"operations/",body,format="json")
        responses=self.concurrent(submit)
        self.assertEqual([r.status_code for r in responses],[200,200])
        self.assertEqual(Event.objects.filter(kind="chat.message").count(),1)
        Delivery.objects.update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        claims=self.concurrent(lambda _:claim())
        self.assertEqual(sum(c is not None for c in claims),1)

class NotificationContractTests(APITestCase):
    def test_schema_matches_response_and_no_private_routing_fields_in_provider_data(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        from .api import preferences_data
        schema=json.loads((Path(__file__).resolve().parents[1]/"contracts/notifications-1/schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        user=__import__('authentication.models',fromlist=['UserModel']).UserModel.objects.create_user(phone_number='+9779800100901')
        pref=Preferences.objects.create(user=user)
        def check(name,value):
            Draft202012Validator({**schema,'$ref':'#/$defs/'+name},format_checker=FormatChecker()).validate(value)
        check('preferences',preferences_data(pref))
        check('push_data',{'schema':'1','delivery_id':str(uuid4()),'expires_at':'2000000000'})
        from jsonschema.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            check('push_data',{'schema':'1','delivery_id':str(uuid4()),'expires_at':'2000000000','user_id':1})
