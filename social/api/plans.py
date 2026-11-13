from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import datetime, time, timedelta
from django.core import signing
from django.db import transaction
from django.db.models import Q, F, DateTimeField
from django.db.models.functions import Cast, Coalesce
from django.db.models.fields.json import KeyTextTransform
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.models import CoupleMembershipModel
from social.services import plans

class PlanFields(StrictSerializer):
    title = serializers.CharField(max_length=120)
    starts_at = serializers.DateTimeField()
    place = serializers.CharField(max_length=240, allow_blank=True, default='')
    time_zone = serializers.CharField(max_length=64, default='UTC')
    def validate_time_zone(self, value):
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError): raise serializers.ValidationError('Choose a valid time zone.')
        return value

class CreateInput(PlanFields):
    request_id = serializers.UUIDField()
    couple = serializers.IntegerField(min_value=1)

class ActionInput(StrictSerializer):
    request_id = serializers.UUIDField()
    expected_revision = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=plans.ACTIONS)
    values = serializers.DictField(default=dict)
    def validate(self, data):
        action = data['action']; values = data['values']
        if action == 'propose': child = PlanFields(data=values)
        elif action == 'add_item': child = ItemInput(data=values)
        elif action == 'set_item': child = CheckInput(data=values)
        elif action == 'remove_item': child = RemoveInput(data=values)
        else:
            if values: raise serializers.ValidationError({'values': 'This action does not accept fields.'})
            return data
        child.is_valid(raise_exception=True); data['values'] = child.validated_data
        return data

class ItemInput(StrictSerializer):
    text = serializers.CharField(max_length=120)
    assigned_to = serializers.IntegerField(min_value=1, allow_null=True, default=None)
class RemoveInput(StrictSerializer):
    item_id = serializers.UUIDField()
class CheckInput(RemoveInput):
    checked = serializers.BooleanField()
class CardInput(StrictSerializer):
    event_id = serializers.RegexField(r'^\$[^\s]{1,254}$')
class PlanThrottle(UserRateThrottle):
    scope = 'plans'; rate = '180/hour'
    def allow_request(self, request, view):
        return request.method in ('GET','HEAD','OPTIONS') or super().allow_request(request, view)

class PlanView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [PlanThrottle]
    lookup_value_regex = '[1-9][0-9]*'
    def audience(self, request):
        member = CoupleMembershipModel.objects.filter(user=request.user, ended_at__isnull=True).first()
        if not member: raise Http404
        return plans.current_couple(request.user, member.couple_id)

    @action(detail=False, methods=['get'])
    def home(self, request):
        try: couple, members = self.audience(request)
        except Http404: return Response({'data': {'couple': None, 'members': [], 'next_plan': None, 'invitations': 0}})
        rows = plans.eligible(couple, members).filter(cancelled_at__isnull=True, completed=False)
        nearest = rows.filter(response='yes', starts_at__gt=timezone.now()).order_by('starts_at','id').first()
        zone = PlanFields().validate_time_zone(request.query_params.get('time_zone', 'UTC'))
        now = timezone.now()
        date = now.astimezone(ZoneInfo(zone)).date()
        start = datetime.combine(date, time.min, tzinfo=ZoneInfo(zone))
        end = datetime.combine(date + timedelta(days=1), time.min, tzinfo=ZoneInfo(zone))
        today = rows.filter(response='yes', starts_at__gte=start, starts_at__lt=end)
        today_plan = today.filter(starts_at__gte=now).order_by('starts_at', 'id').first() or today.order_by('-starts_at', '-id').first()
        proposed = rows.exclude(response='no').annotate(proposal_start=Coalesce(
            Cast(KeyTextTransform('starts_at', 'pending_change'), DateTimeField()), F('starts_at'))).filter(proposal_start__gt=now)
        incoming = proposed.filter((~Q(pending_change={}) & ~Q(pending_change__author=request.user.pk)) |
            (Q(pending_change={}) & Q(response__in=['pending', 'another_time']) & ~Q(created_by=request.user)))
        outgoing = proposed.filter((~Q(pending_change={}) & Q(pending_change__author=request.user.pk)) |
            (Q(pending_change={}) & Q(response__in=['pending', 'another_time']) & Q(created_by=request.user)))
        featured = today_plan or incoming.order_by('proposal_start','id').first() or nearest or outgoing.order_by('proposal_start','id').first()
        return Response({'data': {'couple': couple.pk, 'members': [{'id': m.user_id, 'name': m.user.full_name or 'Your partner', 'photo': request.build_absolute_uri(m.user.profile_photo.url) if m.user.profile_photo else None} for m in members],
            'next_plan': plans.data(nearest, room=False) if nearest else None,
            'featured_plan': plans.data(featured, room=False) if featured else None,
            'requests': rows.exclude(response='no').filter((~Q(pending_change={}) & ~Q(pending_change__author=request.user.pk)) |
                (Q(pending_change={}) & Q(response__in=['pending', 'another_time']) & ~Q(created_by=request.user))).count(),
            'completed_count': plans.eligible(couple, members).filter(completed=True, cancelled_at__isnull=True).count(),
            'invitations': rows.exclude(response='no').filter(~Q(pending_change={}) | Q(response__in=['pending','another_time'], starts_at__gt=timezone.now())).count()}})

    def list(self, request):
        couple, members = self.audience(request)
        rows = plans.eligible(couple, members)
        mode = request.query_params.get('mode','upcoming'); now = timezone.now()
        live = Q(cancelled_at__isnull=True, completed=False) & ~Q(response='no')
        if mode == 'upcoming': rows = rows.filter(live, response='yes', starts_at__gt=now)
        elif mode == 'invitations': rows = rows.filter(live).filter(~Q(pending_change={}) | Q(response__in=['pending','another_time']))
        elif mode == 'past': rows = rows.filter(~live | Q(starts_at__lte=now, pending_change={}))
        else: raise serializers.ValidationError({'mode': 'Choose upcoming, invitations or past.'})
        cursor = request.query_params.get('cursor'); reverse = mode == 'past'
        if cursor:
            try:
                if len(cursor) > 1024: raise ValueError()
                point = signing.loads(cursor, salt='plans-agenda')
                if point['mode'] != mode or point['couple'] != couple.pk: raise ValueError()
                date = parse_datetime(point['date']); pk = int(point['id'])
                if date is None: raise ValueError()
            except (signing.BadSignature, ValueError, KeyError, TypeError): raise serializers.ValidationError({'cursor': 'Refresh the agenda.'})
            rows = rows.filter(Q(starts_at__lt=date) | Q(starts_at=date, pk__lt=pk)) if reverse else rows.filter(Q(starts_at__gt=date) | Q(starts_at=date, pk__gt=pk))
        page = list(rows.order_by('-starts_at','-id')[:21] if reverse else rows.order_by('starts_at','id')[:21])
        next_cursor = signing.dumps({'mode':mode,'couple':couple.pk,'date':page[19].starts_at.isoformat(),'id':page[19].pk},salt='plans-agenda') if len(page)>20 else None
        return Response({'data': {'items': [plans.data(p, room=False) for p in page[:20]], 'next': next_cursor}})

    def create(self, request):
        value = CreateInput(data=request.data); value.is_valid(raise_exception=True)
        fields = dict(value.validated_data); couple = fields.pop('couple'); key = fields.pop('request_id')
        plan, created = plans.create(request.user, key, couple, **fields)
        return Response({'data': plans.data(plan)}, status=201 if created else 200)

    def retrieve(self, request, pk=None):
        return Response({'data': plans.data(plans.plan_for(request.user, pk)[0])})

    @action(detail=True, methods=['post'], url_path='actions')
    def actions(self, request, pk=None):
        value = ActionInput(data=request.data); value.is_valid(raise_exception=True)
        fields = dict(value.validated_data); values = fields.pop('values')
        return Response({'data': plans.data(plans.act(request.user, pk, **fields, **values))})

    @action(detail=False, methods=['get'])
    def reminders(self, request):
        # Complete snapshot for the device's selected IDs; omissions cancel local alarms.
        couple, members = self.audience(request)
        ids = request.query_params.get('ids','').split(',')
        if len(ids)>32 or any(not i.isdecimal() or len(i)>18 for i in ids): raise serializers.ValidationError({'ids':'Choose up to 32 plans.'})
        rows = plans.eligible(couple,members).filter(pk__in=ids, response='yes', completed=False, cancelled_at__isnull=True, starts_at__gt=timezone.now())
        return Response({'data': {'items':[plans.data(p,room=False) for p in rows]}})

    @action(detail=True, methods=['put'])
    def card(self, request, pk=None):
        value = CardInput(data=request.data); value.is_valid(raise_exception=True)
        with transaction.atomic():
            plan, _ = plans.plan_for(request.user, pk, lock=True)
            if plan.created_by_id != request.user.pk: raise Http404
            if not plan.card_event_id:
                plan.card_event_id = value.validated_data['event_id']; plan.save(update_fields=['card_event_id'])
        return Response({'data': plans.data(plan)})
