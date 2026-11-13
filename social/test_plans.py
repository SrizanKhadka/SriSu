from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase
from authentication.models import UserModel
from social.models import CoupleConnectionModel, CouplePlanModel, CoupleMembershipModel, SingleConnectionModel
from social.services.couple_profile_service import create_or_get_couple_for_connection

class PlanTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.one,self.two,self.other=[UserModel.objects.create_user(phone_number=f'+97798000300{i:02}',full_name=f'Plan partner {i}') for i in range(3)]
        connection=CoupleConnectionModel.objects.create(sender_number=self.one.phone_number,receiver_number=self.two.phone_number,connection_status='ACCEPTED')
        self.couple=create_or_get_couple_for_connection(connection)
        self.client.force_authenticate(self.one)
        self.future=timezone.now()+timedelta(days=2)
    def create(self, **updates):
        body=dict(request_id=str(uuid4()),couple=self.couple.pk,title='A walk together',starts_at=self.future.isoformat(),place='Riverside',time_zone='Asia/Kathmandu');body.update(updates)
        r=self.client.post('/api/social/plans/',body,format='json');self.assertEqual(r.status_code,201,r.data)
        return r.data['data'],body
    def act(self,p,action,user=None,status=200,key=None,**values):
        if user: self.client.force_authenticate(user)
        r=self.client.post(f"/api/social/plans/{p['id']}/actions/",dict(request_id=str(key or uuid4()),expected_revision=p['revision'],action=action,values=values),format='json')
        self.assertEqual(r.status_code,status,r.data)
        return r.data.get('data',{})
    def test_both_partners_propose_accept_checklist_complete_undo_and_history(self):
        p,_=self.create(); self.act(p,'accept',status=409)
        p=self.act(p,'accept',self.two);self.assertEqual(p['state'],'agreed')
        p=self.act(p,'add_item',self.one,text='Bring tea',assigned_to=self.two.pk)
        item=p['checklist'][0];p=self.act(p,'set_item',self.two,item_id=item['id'],checked=True)
        self.assertEqual(p['checklist'][0]['checked_by'],self.two.pk)
        changed=self.future+timedelta(hours=2)
        p=self.act(p,'propose',self.one,title=p['title'],starts_at=changed.isoformat(),place='Park',time_zone='Asia/Kathmandu')
        self.assertEqual(p['place'],'Riverside');self.assertEqual(p['response'],'yes');self.assertIn('T',p['pending_change']['starts_at'])
        self.act(p,'accept',self.one,status=409)
        p=self.act(p,'accept',self.two);self.assertEqual(p['place'],'Park');self.assertIsNone(p['pending_change'])
        with patch('django.utils.timezone.now',return_value=changed+timedelta(hours=1)):
            p=self.act(p,'complete',self.one);self.assertEqual(p['completed_by'],self.one.pk)
            p=self.act(p,'undo_complete',self.two);self.assertEqual(p['state'],'elapsed')
            p=self.act(p,'complete',self.two)
            r=self.client.get('/api/social/plans/?mode=past');self.assertEqual(r.data['data']['items'][0]['id'],p['id'])
    def test_reject_or_withdraw_change_keeps_agreement(self):
        p,_=self.create();p=self.act(p,'accept',self.two)
        values=dict(title='Different title',starts_at=(self.future+timedelta(days=1)).isoformat(),place='',time_zone='UTC')
        p=self.act(p,'propose',self.one,**values)
        self.act(p,'propose',self.two,status=409,**values)
        p=self.act(p,'decline',self.two);self.assertEqual(p['response'],'yes');self.assertEqual(p['title'],'A walk together')
        p=self.act(p,'propose',self.two,**values)
        self.act(p,'withdraw_change',self.one,status=409)
        p=self.act(p,'withdraw_change',self.two);self.assertIsNone(p['pending_change'])
    def test_initial_counterproposal_reverses_responder(self):
        p,_=self.create();p=self.act(p,'propose',self.two,title=p['title'],starts_at=self.future.isoformat(),place='Home',time_zone='UTC')
        self.act(p,'accept',self.two,status=409)
        p=self.act(p,'accept',self.one);self.assertEqual(p['state'],'agreed')
    def test_cancel_and_reminder_snapshot_remove_cancelled_plans(self):
        p,_=self.create();p=self.act(p,'accept',self.two)
        path=f"/api/social/plans/reminders/?ids={p['id']}"
        self.assertEqual(len(self.client.get(path).data['data']['items']),1)
        p=self.act(p,'cancel',self.one);self.assertEqual(p['cancelled_by'],self.one.pk)
        self.assertEqual(self.client.get(path).data['data']['items'],[])
        self.act(p,'accept',self.two,status=409)
        self.assertIsNone(self.client.get('/api/social/plans/home/').data['data']['next_plan'])
    def test_create_and_mutation_retries_return_current_state(self):
        p,body=self.create();key=uuid4();before=p
        p=self.act(p,'accept',self.two,key=key)
        p=self.act(p,'cancel',self.one)
        retried=self.act(before,'accept',self.two,key=key);self.assertEqual(retried['state'],'cancelled')
        self.act(before,'decline',self.two,key=key,status=409)
        self.client.force_authenticate(self.one)
        r=self.client.post('/api/social/plans/',body,format='json');self.assertEqual(r.status_code,200);self.assertEqual(r.data['data']['state'],'cancelled')
        body['title']='Different';self.assertEqual(self.client.post('/api/social/plans/',body,format='json').status_code,409)
    def test_legacy_plans_and_routes_preserved_and_stale_clients_cannot_override_change(self):
        old=CouplePlanModel.objects.create(couple=self.couple,created_by=self.one,request_id=uuid4(),title='Existing plan',starts_at=self.future,response='yes',audience_membership_ids=list(self.couple.memberships.order_by('id').values_list('id',flat=True)))
        p=self.client.get(f'/api/social/plans/{old.pk}/').data['data'];self.assertEqual(p['title'],'Existing plan')
        p=self.act(p,'propose',self.one,title=p['title'],starts_at=self.future.isoformat(),place='Park',time_zone='UTC')
        self.client.force_authenticate(self.two)
        r=self.client.patch(f'/api/social/profiles/{self.couple.pk}/plans/{old.pk}/',dict(expected_revision=p['revision'],response='no'),format='json');self.assertEqual(r.status_code,409)
        self.assertEqual(CouplePlanModel.objects.get(pk=old.pk).response,'yes')
    def test_agenda_is_chronological_across_pages_not_creation_order(self):
        for day in range(24,0,-1):
            p,_=self.create(starts_at=(self.future+timedelta(days=day)).isoformat());self.act(p,'accept',self.two);self.client.force_authenticate(self.one)
        first=self.client.get('/api/social/plans/').data['data'];self.assertEqual(len(first['items']),20)
        second=self.client.get('/api/social/plans/',{'cursor':first['next']}).data['data']
        values=first['items']+second['items'];self.assertEqual(len({p['id'] for p in values}),24)
        self.assertEqual([p['starts_at'] for p in values],sorted(p['starts_at'] for p in values))
        self.assertEqual(self.client.get('/api/social/plans/',{'mode':'past','cursor':first['next']}).status_code,400)
    def test_permissions_validation_and_expired_session(self):
        p,_=self.create()
        self.act(p,'add_item',self.one,status=409,text='Not agreed')
        p=self.act(p,'accept',self.two)
        self.act(p,'add_item',status=400,text='Private',assigned_to=self.other.pk)
        self.act(p,'propose',status=400,title='Test',starts_at=self.future.isoformat(),time_zone='Not/AZone')
        self.act(p,'cancel',status=400,unexpected='value')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/social/plans/{p['id']}/").status_code,404)
        self.act(p,'cancel',status=404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/social/plans/').status_code,401)
    def test_responses_and_routes_match_plans_contract(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator
        from django.urls import resolve
        schema=json.loads((Path(__file__).resolve().parents[1]/'contracts/plans-1/openapi.json').read_text())
        p,_=self.create()
        for path,methods in schema['paths'].items():
            match=resolve(path.replace('{id}',str(p['id'])))
            for method in methods:self.assertIn(method,match.func.actions)
        for path,name in [('home/','HomeResponse'),('','PageResponse'),(f"{p['id']}/",'PlanResponse'),(f"reminders/?ids={p['id']}",'ReminderSnapshotResponse')]:
            Draft202012Validator(schema|{'$ref':'#/components/schemas/'+name}).validate(self.client.get('/api/social/plans/'+path).json())
        p=self.act(p,'propose',self.two,title=p['title'],starts_at=self.future.isoformat(),place='Home',time_zone='UTC')
        Draft202012Validator(schema|{'$ref':'#/components/schemas/PlanResponse'}).validate(self.client.get(f"/api/social/plans/{p['id']}/").json())
    def test_block_and_membership_replacement_hide_original_plans(self):
        p,_=self.create()
        # Use the application's existing block model field names.
        block=SingleConnectionModel.objects.create(sender_number=self.two.phone_number, receiver_number=self.one.phone_number, connection_status='BLOCKED')
        self.assertEqual(self.client.get(f"/api/social/plans/{p['id']}/").status_code,404)
        block.delete()
        member=self.couple.memberships.get(user=self.two);position=member.position;member.delete()
        CoupleMembershipModel.objects.create(couple=self.couple,user=self.two,position=position)
        self.assertEqual(self.client.get(f"/api/social/plans/{p['id']}/").status_code,404)

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from social.services import plans

@skipUnless(connection.vendor=='postgresql','Requires isolated PostgreSQL row locks')
class PlanConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.client=APIClient();PlanTests.setUp(self)
    def race(self,*actions):
        barrier=Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:return action()
                except plans.PlanConflict:return 'conflict'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:return list(pool.map(run,actions))
    def initial(self):
        return plans.create(self.one,uuid4(),self.couple.pk,title='Concurrent plan',starts_at=self.future,place='',time_zone='UTC')[0]
    def test_duplicate_creation_returns_one_plan(self):
        key=uuid4()
        self.race(*[lambda:plans.create(self.one,key,self.couple.pk,title='One plan',starts_at=self.future,place='',time_zone='UTC') for _ in range(2)])
        self.assertEqual(CouplePlanModel.objects.count(),1)
    def test_accept_racing_cancellation_cannot_overwrite_revision(self):
        p=self.initial()
        result=self.race(lambda:plans.act(self.one,p.pk,uuid4(),p.revision,'cancel'),lambda:plans.act(self.two,p.pk,uuid4(),p.revision,'accept'))
        self.assertEqual(result.count('conflict'),1);p.refresh_from_db();self.assertEqual(p.revision,2)
    def test_concurrent_checklist_updates_require_review_and_retry(self):
        p=self.initial();p=plans.act(self.two,p.pk,uuid4(),p.revision,'accept')
        result=self.race(*[lambda user=user:plans.act(user,p.pk,uuid4(),p.revision,'add_item',text='Bring something',assigned_to=None) for user in [self.one,self.two]])
        self.assertEqual(result.count('conflict'),1);p.refresh_from_db();self.assertEqual(len(p.checklist),1)
    def test_operation_receipt_failure_rolls_back_changes(self):
        p=self.initial()
        with patch('social.services.plans.PlanOperation.objects.create',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):plans.act(self.two,p.pk,uuid4(),p.revision,'accept')
        p.refresh_from_db();self.assertEqual(p.response,'pending');self.assertEqual(p.revision,1)

class PlanMigrationPreservationTest(TransactionTestCase):
    def test_additive_migration_keeps_existing_ids_details_and_audience(self):
        from django.db.migrations.executor import MigrationExecutor
        before_target=[('social','0019_challenge_catalogue')]
        after_target=[('social','0020_coupleplanmodel_cancelled_at_and_more')]
        executor=MigrationExecutor(connection);executor.migrate(before_target)
        try:
            self.client=APIClient();PlanTests.setUp(self)
            old=executor.loader.project_state(before_target).apps.get_model('social','CouplePlanModel')
            row=old.objects.create(couple_id=self.couple.pk,created_by_id=self.one.pk,request_id=uuid4(),title='Preserved synthetic plan',starts_at=self.future,response='yes',response_note='Original note',completed=False,revision=9,audience_membership_ids=list(self.couple.memberships.order_by('id').values_list('id',flat=True)))
            fields=['id','couple_id','created_by_id','title','starts_at','response','response_note','completed','revision','request_id','audience_membership_ids']
            expected=old.objects.filter(pk=row.pk).values(*fields).get()
        finally:
            MigrationExecutor(connection).migrate(after_target)
        self.assertEqual(CouplePlanModel.objects.filter(pk=row.pk).values(*fields).get(),expected)
        migrated=CouplePlanModel.objects.get(pk=row.pk)
        self.assertEqual(migrated.time_zone,'');self.assertEqual(migrated.checklist,[]);self.assertEqual(migrated.pending_change,{})
