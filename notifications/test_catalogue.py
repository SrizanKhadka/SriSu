"""Cross-domain notification transitions, authorization and mixed-client rollout."""
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase
from social.models import ChallengeTemplate, CoupleMomentModel, CoupleMomentNoteModel, SingleConnectionModel
from social.services import plans, challenges, sparks, date_ideas
from .catalogue import CAPABILITY, CATEGORY_DEFAULTS
from .cards import MARKER
from .models import CardBinding, Delivery, Event, Installation, Preferences
from .services import destination
from .provider import message
from .worker import run_once
from .test_matrix_push import MatrixFixtures, SETTINGS


@override_settings(**SETTINGS)
class CatalogueTests(MatrixFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.bind(version=3, capabilities=["matrix-chat-push-1", CAPABILITY]).status_code, 200)

    def register_other(self, user):
        name = "first" if user==self.first else "third"
        response = self.bind(getattr(self,name+"_client"), installation_id=str(uuid4()),
            target="synthetic-target-"+name, capabilities=["matrix-chat-push-1",CAPABILITY])
        self.assertEqual(response.status_code,200)

    def resolve(self, kind, user=None):
        user=user or self.second
        event=Event.objects.filter(kind=kind,recipient=user).latest("created_at")
        return destination(event,user,getattr(self,"first_sid" if user==self.first else "second_sid"))

    def reserve(self, kind, resource, client=None):
        return (client or self.first_client).post("/api/notifications/v1/cards/",
            {"kind":kind,"resource":str(resource)},format="json")

    def challenge(self):
        template=ChallengeTemplate.objects.filter(published=True).first()
        return challenges.start(self.first,uuid4(),self.couple.pk,template.pk)[0]

    def moment(self, visibility="public"):
        response=self.first_client.post("/api/social/couple-moments/",
            {"couple":self.couple.pk,"caption":"Synthetic private content","visibility":visibility},format="json")
        self.assertEqual(response.status_code,201,response.data)
        return CoupleMomentModel.objects.latest("pk")

    def test_preferences_defaults_partial_legacy_write_and_conflict(self):
        path="/api/notifications/v1/preferences/"
        original=self.second_client.get(path).data["data"]
        self.assertEqual(original["categories"],CATEGORY_DEFAULTS)
        payload=dict(expected_revision=1,push=True,chat=True,activity=True,mute="unchanged",categories={"plans":False,"status":True})
        self.assertEqual(self.second_client.put(path,payload,format="json").status_code,200)
        self.assertEqual(self.second_client.put(path,payload,format="json").status_code,409)
        payload.pop("categories");payload["expected_revision"]=2
        saved=self.second_client.put(path,payload,format="json").data["data"]
        self.assertFalse(saved["categories"]["plans"]);self.assertTrue(saved["categories"]["status"])
        payload.update(expected_revision=3,categories={"invented":True})
        self.assertEqual(self.second_client.put(path,payload,format="json").status_code,400)
        self.plan();self.assertFalse(Delivery.objects.exists())

    def test_plan_proposed_future_after_original_elapsed_and_checklist_revision(self):
        self.register_other(self.first)
        plan=self.plan()
        plans.act(self.second,plan.pk,uuid4(),plan.revision,"accept")
        event=Event.objects.get(kind="plan.responded")
        plan.refresh_from_db()
        plans.act(self.first,plan.pk,uuid4(),plan.revision,"add_item",text="Synthetic preparation",assigned_to=None)
        self.assertEqual(destination(event,self.first,self.first_sid)["plan_id"],plan.pk)
        plan.refresh_from_db();plan.starts_at=timezone.now()-timedelta(minutes=1);plan.save()
        plans.act(self.second,plan.pk,uuid4(),plan.revision,"propose",**self.values)
        latest=Event.objects.filter(kind="plan.responded").latest("created_at")
        self.assertGreater(latest.expires_at,timezone.now())
        self.assertEqual(destination(latest,self.first,self.first_sid)["plan_id"],plan.pk)
        plan.refresh_from_db();plans.act(self.first,plan.pk,uuid4(),plan.revision,"cancel")
        self.assertEqual(self.resolve("plan.cancelled")["plan_id"],plan.pk)
        with self.assertRaises(NotFound):destination(latest,self.first,self.first_sid)

    def test_decline_outcome_survives_old_time_but_does_not_resurrect_invitation(self):
        self.register_other(self.first);plan=self.plan()
        plan.starts_at=timezone.now()-timedelta(seconds=1);plan.save()
        plans.act(self.second,plan.pk,uuid4(),plan.revision,"decline")
        self.assertEqual(self.resolve("plan.responded",self.first)["plan_id"],plan.pk)
        with self.assertRaises(NotFound):self.resolve("plan.created")

    def test_declining_future_change_to_elapsed_agreed_plan_still_delivers_outcome(self):
        self.register_other(self.first)
        plan=self.plan();plan.response="yes";plan.starts_at=timezone.now()-timedelta(minutes=1);plan.save()
        plan=plans.act(self.second,plan.pk,uuid4(),plan.revision,"propose",**self.values)
        plans.act(self.first,plan.pk,uuid4(),plan.revision,"decline")
        outcome=Event.objects.filter(kind="plan.responded",recipient=self.second).get()
        self.assertGreater(outcome.expires_at,timezone.now())
        self.assertEqual(destination(outcome,self.second,self.second_sid)["plan_id"],plan.pk)

    def test_card_reservation_precedes_matrix_and_is_retry_safe_without_ack(self):
        plan=self.plan();r=self.reserve("plan",plan.pk);self.assertEqual(r.status_code,200)
        key=r.data["data"]["key"]
        self.assertEqual(self.reserve("plan",plan.pk).data["data"]["key"],key)
        self.event["content"][MARKER]=key
        self.assertEqual(self.post_transaction().status_code,200)
        self.assertEqual(self.post_transaction("retry").status_code,200)
        self.assertEqual(Delivery.objects.count(),1)
        self.assertEqual(Delivery.objects.get().event.kind,"plan.created")
        self.assertEqual(CardBinding.objects.get().event_id,self.event["event_id"])
        self.assertNotIn("ciphertext",str(Event.objects.get(kind="matrix.message").context))
        self.assertEqual(self.reserve("plan",plan.pk,self.second_client).status_code,404)
        self.assertEqual(self.reserve("plan",plan.pk,self.third_client).status_code,404)

    def test_card_marker_cannot_be_forged_reused_or_moved_to_new_epoch(self):
        plan=self.plan();key=self.reserve("plan",plan.pk).data["data"]["key"]
        self.event["content"][MARKER]=key
        self.post_transaction()
        self.event["event_id"]="$different-message"
        self.post_transaction("reused")
        self.assertEqual(Delivery.objects.filter(event__kind="matrix.message").count(),1)
        self.event["event_id"]="$forged-message";self.event["content"][MARKER]=str(uuid4())
        self.post_transaction("forged")
        self.assertEqual(Delivery.objects.filter(event__kind="matrix.message").count(),2)
        self.mapping.membership_epoch+=1;self.mapping.save()
        self.event["event_id"]="$new-epoch";self.event["content"][MARKER]=key
        self.post_transaction("epoch")
        self.assertEqual(Delivery.objects.filter(event__kind="matrix.message").count(),3)

    def test_category_off_suppresses_tagged_card_instead_of_bypassing_preference(self):
        Preferences.objects.create(user=self.second,categories={"plans":False})
        plan=self.plan();self.event["content"][MARKER]=self.reserve("plan",plan.pk).data["data"]["key"]
        self.post_transaction();self.ready();sender=Mock();run_once(sender)
        sender.assert_not_called();self.assertFalse(Delivery.objects.exists())

    def test_challenge_card_falls_back_to_chat_for_legacy_recipient_only(self):
        self.bind(version=4,capabilities=["matrix-chat-push-1"])
        challenge=self.challenge()
        self.assertFalse(Delivery.objects.exists())
        self.event["content"][MARKER]=self.reserve("challenge",challenge.pk).data["data"]["key"]
        self.post_transaction()
        self.assertEqual(Delivery.objects.get().event.kind,"matrix.message")

    def test_challenge_lifecycle_only_emits_meaningful_changes(self):
        self.register_other(self.first);challenge=self.challenge()
        self.assertEqual(self.resolve("challenge.invited")["challenge_id"],str(challenge.pk))
        self.event["content"][MARKER]=self.reserve("challenge",challenge.pk).data["data"]["key"]
        self.post_transaction();self.assertFalse(Delivery.objects.filter(event__kind="matrix.message").exists())
        key=uuid4();challenges.act(self.second,challenge.pk,key,"accept",1)
        challenges.act(self.second,challenge.pk,key,"accept",1)
        self.assertEqual(Event.objects.filter(kind="challenge.responded").count(),1)
        with self.assertRaises(NotFound):self.resolve("challenge.invited")
        challenges.act(self.first,challenge.pk,uuid4(),"confirm",2)
        self.assertFalse(Delivery.objects.filter(event__kind="challenge.progress").exists())
        challenges.act(self.second,challenge.pk,uuid4(),"confirm",3)
        self.assertEqual(self.resolve("challenge.completed",self.first)["challenge_id"],str(challenge.pk))
        self.assertTrue(Event.objects.get(kind="challenge.completed").quiet)

    def test_challenge_progress_opt_in_and_active_cancellation(self):
        self.register_other(self.first)
        Preferences.objects.create(user=self.second,categories={"challenge_progress":True})
        challenge=self.challenge()
        challenges.act(self.second,challenge.pk,uuid4(),"accept",1)
        challenges.act(self.first,challenge.pk,uuid4(),"pause",2)
        self.assertEqual(self.resolve("challenge.progress")["challenge_id"],str(challenge.pk))
        challenges.act(self.first,challenge.pk,uuid4(),"pause",3)
        self.assertEqual(Event.objects.filter(kind="challenge.progress").count(),1)
        challenges.act(self.second,challenge.pk,uuid4(),"cancel",4)
        self.assertEqual(self.resolve("challenge.cancelled",self.first)["challenge_id"],str(challenge.pk))

    def test_sharing_approval_is_once_quiet_and_revocation_invalidates(self):
        self.register_other(self.first)
        path=f"/api/social/profiles/{self.couple.pk}/"
        def change(client,action,**values):
            revision=client.get(path).data["data"]["revisions"]["sharing"]
            response=client.patch(path+"sections/sharing/",dict(expected_revision=revision,action=action,**values),format="json")
            self.assertEqual(response.status_code,200,response.data)
        change(self.first_client,"propose",sections=["story"])
        change(self.second_client,"approve")
        self.assertEqual(self.resolve("sharing.approved",self.first)["kind"],"sharing")
        change(self.second_client,"approve")
        self.assertEqual(Event.objects.filter(kind="sharing.approved").count(),1)
        self.assertTrue(Event.objects.get(kind="sharing.approved").quiet)
        change(self.first_client,"revoke")
        with self.assertRaises(NotFound):self.resolve("sharing.approved",self.first)

    def test_spark_first_answer_opt_in_and_mutual_reveal_are_quiet(self):
        self.register_other(self.first)
        Preferences.objects.create(user=self.second,categories={"spark_progress":True})
        spark,_=sparks.start(self.first,uuid4(),self.couple.pk,"deep-1-v1")
        envelope={"algorithm":"m.megolm.v1.aes-sha2","ciphertext":"A"*256,"session_id":"B"*43}
        sparks.submit(self.first,spark.pk,envelope)
        self.assertEqual(self.resolve("spark.submitted")["spark_id"],str(spark.pk))
        sparks.submit(self.second,spark.pk,envelope)
        self.assertEqual(self.resolve("spark.revealed",self.first)["spark_id"],str(spark.pk))
        with self.assertRaises(NotFound):self.resolve("spark.submitted")
        self.assertTrue(Event.objects.get(kind="spark.revealed").quiet)

    def test_spark_manual_is_quiet_automatic_is_silent_and_card_is_deduplicated(self):
        spark,_=sparks.start(self.first,uuid4(),self.couple.pk,"deep-1-v1")
        self.assertTrue(Event.objects.get(kind="spark.started").quiet)
        self.event["content"][MARKER]=self.reserve("spark",spark.pk).data["data"]["key"]
        self.post_transaction();self.assertEqual(Delivery.objects.count(),1)
        sparks.skip(self.first,spark.pk)
        with self.assertRaises(NotFound):self.resolve("spark.started")
        Event.objects.all().delete()
        sparks.start(self.first,uuid4(),self.couple.pk,"deep-2-v1",automatic=True)
        self.assertFalse(Event.objects.exists())

    def test_moment_publication_is_quiet_and_reaction_opt_in_deduplicates(self):
        self.register_other(self.first)
        Preferences.objects.create(user=self.first,categories={"moment_reactions":True})
        moment=self.moment()
        self.assertEqual(self.resolve("moment.published")["moment_id"],moment.pk)
        self.ready();sender=Mock();run_once(sender);self.assertFalse(sender.call_args.kwargs["audible"])
        self.assertEqual(message(Delivery.objects.get(),audible=False).android.priority,"normal")
        self.assertIsNone(Installation.objects.get(user=self.second).last_alert_at)
        url=f"/api/social/couple-moments/{moment.pk}/reaction/"
        for _ in range(2):self.assertEqual(self.second_client.put(url,{"reacted":True},format="json").status_code,200)
        self.assertEqual(Event.objects.filter(kind="moment.hearted").count(),1)
        self.assertEqual(self.resolve("moment.hearted",self.first)["moment_id"],moment.pk)
        self.second_client.put(url,{"reacted":False},format="json")
        with self.assertRaises(NotFound):self.resolve("moment.hearted",self.first)
        moment.expires_at=timezone.now()-timedelta(seconds=1);moment.save()
        with self.assertRaises(NotFound):self.resolve("moment.published")

    def test_appreciation_and_visitor_reply_remain_private_after_moment_expiry(self):
        self.register_other(self.third)
        Preferences.objects.create(user=self.second,categories={"appreciations":True})
        moment=self.moment()
        response=self.third_client.post(f"/api/social/couple-moments/{moment.pk}/notes/",{"message":"Synthetic appreciation"},format="json")
        self.assertEqual(response.status_code,201,response.data)
        note=CoupleMomentNoteModel.objects.get()
        self.assertEqual(self.resolve("moment.appreciated")["note_id"],note.pk)
        self.assertEqual(Delivery.objects.filter(event__kind="moment.appreciated").count(),1)
        moment.expires_at=timezone.now()-timedelta(seconds=1);moment.save()
        response=self.second_client.post(f"/api/social/moment-notes/{note.pk}/replies/",{"message":"Synthetic reply"},format="json")
        self.assertEqual(response.status_code,201,response.data)
        event=Event.objects.get(kind="moment.replied")
        self.assertEqual(destination(event,self.third,self.third_sid),{"kind":"moment_note","note_id":note.pk})
        detail=self.third_client.get(f"/api/social/moment-notes/{note.pk}/").data
        self.assertFalse(detail["can_reply"]);self.assertEqual(len(detail["replies"]),1)
        self.assertTrue(self.second_client.get(f"/api/social/moment-notes/{note.pk}/").data["can_reply"])
        with self.assertRaises(NotFound):destination(event,self.first,self.first_sid)
        SingleConnectionModel.objects.create(sender_number=self.third.phone_number,receiver_number=self.first.phone_number,connection_status="BLOCKED")
        with self.assertRaises(NotFound):destination(event,self.third,self.third_sid)

    def test_status_opt_in_hourly_limit_and_clear_invalidates(self):
        Preferences.objects.create(user=self.second,categories={"status":True})
        from social.api.couple_status import STATUSES
        texts=list(STATUSES)
        for revision in range(2):
            response=self.first_client.put("/api/social/couple-status/mine/",{"couple":self.couple.pk,
                "text":texts[revision],"expected_revision":revision,"request_id":str(uuid4())},format="json")
            self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(Delivery.objects.filter(event__kind="status.changed").count(),1)
        self.assertEqual(self.resolve("status.changed"),{"kind":"home"})
        self.first_client.put("/api/social/couple-status/mine/",{"couple":self.couple.pk,"text":"","expected_revision":2,"request_id":str(uuid4())},format="json")
        with self.assertRaises(NotFound):self.resolve("status.changed")

    def test_ideas_opt_in_mutual_interest_and_conversion_only_emit_plan(self):
        self.register_other(self.first)
        for user in (self.first,self.second):Preferences.objects.create(user=user,categories={"ideas":True})
        idea,_=date_ideas.create(self.first,uuid4(),self.couple.pk,"coffee")
        self.assertEqual(self.resolve("idea.created")["idea_id"],str(idea.pk))
        date_ideas.act(self.second,idea.pk,uuid4(),1,"interest",{"interested":True})
        self.assertEqual(self.resolve("idea.interested",self.first)["idea_id"],str(idea.pk))
        date_ideas.act(self.first,idea.pk,uuid4(),2,"plan",self.values)
        with self.assertRaises(NotFound):self.resolve("idea.created")
        self.assertEqual(Event.objects.filter(kind="plan.created").count(),1)

    def test_os_tags_separate_chat_resources_and_notes_from_moments(self):
        first=self.plan();second=self.plan();self.post_transaction()
        rows=list(Delivery.objects.select_related("event","installation"))
        self.assertEqual(len({message(row,audible=True).android.notification.tag for row in rows}),3)
        row=rows[0];row.event.kind="moment.published";row.event.resource="1"
        tag=message(row,audible=False).android.notification.tag
        row.event.kind="moment.replied"
        self.assertNotEqual(tag,message(row,audible=False).android.notification.tag)

    def test_transaction_rollback_leaves_no_event_or_delivery(self):
        try:
            with transaction.atomic():
                self.plan();raise RuntimeError("Synthetic rollback")
        except RuntimeError:pass
        self.assertFalse(Event.objects.exists());self.assertFalse(Delivery.objects.exists())
