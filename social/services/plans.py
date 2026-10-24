"""Existing couple plans, extended without replacing identifiers or audiences."""
import json
from uuid import uuid4
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError
from social.models import CouplePlanModel, PlanOperation
from social.services.moment_publication import current_couple

ACTIONS = ('accept', 'decline', 'propose', 'withdraw_change', 'cancel', 'complete', 'undo_complete', 'add_item', 'set_item', 'remove_item')

class PlanConflict(APIException):
    status_code = 409
    default_code = 'plan_changed'
    default_detail = 'This plan changed. Refresh and review the latest details.'


def eligible(couple, members):
    return CouplePlanModel.objects.filter(couple=couple, audience_membership_ids=[m.pk for m in members])


def plan_for(user, pk, *, lock=False):
    initial = CouplePlanModel.objects.filter(pk=pk).first()
    if not initial:
        raise Http404
    couple, members = current_couple(user, initial.couple_id, lock=lock)
    rows = eligible(couple, members)
    plan = (rows.select_for_update() if lock else rows).filter(pk=pk).first()
    if not plan:
        raise Http404
    return plan, members


def payload(values):
    return json.loads(json.dumps(values, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v), sort_keys=True))


def state(plan):
    if plan.cancelled_at: return 'cancelled'
    if plan.completed: return 'completed'
    if plan.response == 'no': return 'declined'
    if plan.pending_change: return 'change_proposed'
    if plan.starts_at <= timezone.now(): return 'elapsed'
    return 'agreed' if plan.response == 'yes' else 'invited'


def data(plan, *, room=True):
    from chat.models import ChatRoom, MatrixRoomMapping
    chat = ChatRoom.objects.filter(couple_id=plan.couple_id).first() if room else None
    mapping = MatrixRoomMapping.objects.filter(chat_room=chat, state='active', replacement_pending=False).first() if chat else None
    return {'id': plan.pk, 'couple': plan.couple_id, 'creator': plan.created_by_id,
        'title': plan.title, 'starts_at': plan.starts_at, 'time_zone': plan.time_zone, 'place': plan.place,
        'response': plan.response, 'response_note': plan.response_note, 'state': state(plan), 'revision': plan.revision,
        'pending_change': plan.pending_change or None, 'checklist': plan.checklist,
        'cancelled_at': plan.cancelled_at, 'cancelled_by': plan.cancelled_by_id,
        'completed_at': plan.completed_at, 'completed_by': plan.completed_by_id,
        'room_id': str(chat.pk) if mapping else None, 'matrix_room_id': mapping.matrix_room_id if mapping else None,
        'membership_epoch': mapping.membership_epoch if mapping else None, 'card_event_id': plan.card_event_id}


@transaction.atomic
def create(user, request_id, couple_id, **values):
    couple, members = current_couple(user, couple_id, lock=True)
    original = payload(values)
    existing = eligible(couple, members).filter(created_by=user, request_id=request_id).first()
    if existing:
        recorded = existing.creation_payload or payload({'title': existing.title, 'starts_at': existing.starts_at, 'place': existing.place, 'time_zone': existing.time_zone})
        if recorded != original: raise PlanConflict('This request was already used for a different plan.')
        return existing, False
    if values['starts_at'] <= timezone.now():
        raise ValidationError({'starts_at': 'Choose a future time.'})
    return CouplePlanModel.objects.create(couple=couple, created_by=user, request_id=request_id,
        audience_membership_ids=[m.pk for m in members], creation_payload=original, **values), True


@transaction.atomic
def act(user, pk, request_id, expected_revision, action, **values):
    plan, members = plan_for(user, pk, lock=True)
    intent = payload(dict(action=action, expected_revision=expected_revision, **values))
    receipt = plan.operations.filter(author=user, request_id=request_id).first()
    if receipt:
        if receipt.payload != intent: raise PlanConflict('This request was already used for another action.')
        return plan
    if plan.revision != expected_revision: raise PlanConflict()
    if plan.cancelled_at or plan.response == 'no': raise PlanConflict('This plan has ended.')
    if plan.completed and action != 'undo_complete': raise PlanConflict('Undo completion before changing this plan.')
    now = timezone.now()
    change = plan.pending_change
    proposer = change.get('author') if change else plan.created_by_id
    if action in ('accept', 'decline'):
        if (not change and plan.response == 'yes') or proposer == user.pk:
            raise PlanConflict('Only the other partner can respond to this proposal.')
        if action == 'accept':
            date = parse_datetime(change['starts_at']) if change else plan.starts_at
            if date <= now: raise PlanConflict('That time has passed. Suggest another time.')
            if change:
                for key in ('title', 'place', 'time_zone'): setattr(plan, key, change[key])
                plan.starts_at = date
            plan.response = 'yes'
        elif not change or plan.response != 'yes':
            plan.response = 'no'
        plan.pending_change = {}
    elif action == 'propose':
        if change and proposer != user.pk: raise PlanConflict('Respond to the pending change first.')
        if values['starts_at'] <= now: raise ValidationError({'starts_at': 'Choose a future time.'})
        plan.pending_change = dict(payload(values), author=user.pk)
    elif action == 'withdraw_change':
        if not change or proposer != user.pk: raise PlanConflict('Only the person who suggested this change can withdraw it.')
        plan.pending_change = {}
    elif action == 'cancel':
        plan.cancelled_at = now; plan.cancelled_by = user; plan.pending_change = {}
    elif action in ('complete', 'undo_complete'):
        if plan.response != 'yes' or plan.starts_at > now or change:
            raise PlanConflict('Only an agreed past plan without a pending change can be marked done.')
        if action == 'undo_complete' and not plan.completed: raise PlanConflict()
        plan.completed = action == 'complete'
        plan.completed_at = now if plan.completed else None
        plan.completed_by = user if plan.completed else None
    elif action in ('add_item', 'set_item', 'remove_item'):
        if plan.response != 'yes': raise PlanConflict('Agree on the plan before preparing your checklist.')
        if action == 'add_item':
            if len(plan.checklist) >= 30: raise ValidationError({'text': 'A plan can have up to 30 checklist items.'})
            assigned = values['assigned_to']
            if assigned is not None and assigned not in [m.user_id for m in members]: raise ValidationError({'assigned_to': 'Choose one of you.'})
            plan.checklist.append({'id': str(uuid4()), 'text': values['text'], 'assigned_to': assigned, 'checked': False, 'checked_by': None})
        else:
            item = next((i for i in plan.checklist if i['id'] == str(values['item_id'])), None)
            if not item: raise PlanConflict('This item was removed. Refresh your checklist.')
            if action == 'remove_item': plan.checklist.remove(item)
            else: item.update(checked=values['checked'], checked_by=user.pk if values['checked'] else None)
    else: raise ValidationError({'action': 'Choose a supported action.'})
    plan.revision += 1
    plan.save()
    PlanOperation.objects.create(plan=plan, author=user, request_id=request_id, payload=intent)
    return plan
