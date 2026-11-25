"""Current-state authorization for notification destinations and domain producers."""
import json
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound


def plan_signature(plan):
    # Checklist revisions do not supersede a response. Private values are never stored.
    value = {key: getattr(plan, key) for key in ("title", "starts_at", "place", "time_zone",
        "response", "response_note", "pending_change", "cancelled_at", "completed")}
    return salted_hmac("notification.plan", json.dumps(value, sort_keys=True, default=str)).hexdigest()


def plan_deadline(plan):
    proposal = parse_datetime(plan.pending_change.get("starts_at", "")) if plan.pending_change else None
    return max(plan.starts_at, proposal) if proposal else plan.starts_at


def plan_event(kind, actor, plan, source, *, quiet=None, action=None):
    from .services import partner_event
    # A cancellation/decline describes an outcome, not a still-actionable invitation.
    outcome = kind == "plan.cancelled" or plan.response == "no" or action in ("decline", "withdraw_change")
    deadline = None if outcome else plan_deadline(plan)
    return partner_event(kind, actor, plan.couple, plan.pk, source, version=plan.revision,
        context={"plan_signature": plan_signature(plan), "action": action}, expires_at=deadline, quiet=quiet)


def plan_destination(event, couple, audience):
    from social.models import CouplePlanModel
    plan = CouplePlanModel.objects.filter(pk=event.resource, couple=couple, audience_membership_ids=audience).first()
    if plan is None:
        raise NotFound()
    if event.kind == "plan.cancelled":
        if not plan.cancelled_at:
            raise NotFound()
    else:
        if plan.cancelled_at or plan.completed:
            raise NotFound()
        outcome = event.kind == "plan.responded" and (plan.response == "no" or event.context.get("action") in ("decline", "withdraw_change"))
        if not outcome and plan_deadline(plan) <= timezone.now():
            raise NotFound()
        if event.kind == "plan.created" and plan.response == "no":
            raise NotFound()
    signature = event.context.get("plan_signature")
    if signature:
        if signature != plan_signature(plan):
            raise NotFound()
    elif event.kind == "plan.responded" and plan.revision != event.resource_version:
        raise NotFound()  # Preserve already queued legacy-event behavior.
    return {"kind": "plan", "couple_id": couple.pk, "plan_id": plan.pk}


def note_destination(event, user):
    from social.services.moment_service import visible_notes
    note = visible_notes(user).filter(pk=event.resource, moment__couple_id=event.couple_id).first()
    if note is None or note.recipient_membership_ids != event.audience_membership_ids:
        raise NotFound()
    if event.kind == "moment.appreciated" and user.pk not in note.recipient_user_ids:
        raise NotFound()
    if event.kind == "moment.replied" and (user.pk != note.sender_id or
            not note.replies.filter(pk=event.context.get("reply_id")).exists()):
        raise NotFound()
    return {"kind": "moment_note", "note_id": note.pk}


def extended_destination(event, user, couple, audience):
    if event.kind.startswith("challenge."):
        from social.services.challenges import challenge_for
        value = challenge_for(user, event.resource)
        if value.couple_id != couple.pk or value.revision != event.resource_version:
            raise NotFound()
        expected = event.context.get("state")
        if value.state != expected:
            raise NotFound()
        return {"kind": "challenge", "challenge_id": str(value.pk)}
    if event.kind in ("moment.published", "moment.hearted"):
        from social.services.moment_service import eligible_moments
        moment = eligible_moments(user).filter(pk=event.resource, couple=couple).first()
        if moment is None:
            raise NotFound()
        if event.kind == "moment.hearted" and not moment.reactions.filter(user_id=event.context.get("actor_id")).exists():
            raise NotFound()
        return {"kind": "moment", "moment_id": moment.pk, "couple_id": couple.pk}
    if event.kind == "status.changed":
        from social.models import CoupleStatus
        status = CoupleStatus.objects.filter(pk=event.resource, couple=couple,
            audience_membership_ids=audience, expires_at__gt=timezone.now()).exclude(text="").first()
        if status is None or status.author_id == user.pk:
            raise NotFound()
        return {"kind": "home"}  # Current status, not an obsolete revision's text.
    if event.kind.startswith("idea."):
        from social.services.date_ideas import idea_for
        idea, _ = idea_for(user, event.resource)
        if idea.couple_id != couple.pk or idea.state != "open":
            raise NotFound()
        if event.kind == "idea.interested" and len(idea.interested_by) != 2:
            raise NotFound()
        return {"kind": "idea", "idea_id": str(idea.pk)}
    raise NotFound()


def nickname_member(event, user, *, members=None):
    """Resolve the exact current alias; never archive chosen names in event metadata."""
    from django.http import Http404
    from social.services.moment_publication import current_couple
    if event.recipient_id != user.pk or event.kind != "nickname.changed":
        raise NotFound()
    if members is None:
        try:
            _, members = current_couple(user, event.couple_id)
        except Http404:
            raise NotFound() from None
    if [m.pk for m in members] != event.audience_membership_ids:
        raise NotFound()
    member = next((m for m in members if str(m.pk) == event.resource and m.user_id != user.pk), None)
    if (member is None or member.user_id != event.context.get("actor_id")
            or member.partner_nickname_revision != event.resource_version or not member.partner_nickname):
        raise NotFound()
    return member
