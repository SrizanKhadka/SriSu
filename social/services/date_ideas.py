"""Original-couple date ideas. Interest never means acceptance of a dated Plan."""
from django.db import transaction
from django.http import Http404
from rest_framework.exceptions import ValidationError
from social.models import DateIdea, DateIdeaOperation
from social.services import plans
from social.services.moment_publication import current_couple

PRESETS = ("coffee", "walk", "movie")


def eligible(couple, members):
    return DateIdea.objects.filter(couple=couple, audience_membership_ids=[m.pk for m in members])


def data(idea):
    return {"id": str(idea.pk), "couple": idea.couple_id, "creator": idea.creator_id,
        "preset": idea.preset, "interested_by": idea.interested_by, "state": idea.state,
        "revision": idea.revision, "created_at": idea.created_at, "plan_id": idea.plan_id}


def idea_for(user, pk, *, lock=False):
    initial = DateIdea.objects.filter(pk=pk).first()
    if not initial:
        raise Http404
    couple, members = current_couple(user, initial.couple_id, lock=lock)
    idea = eligible(couple, members).filter(pk=pk).first()
    if idea is None:
        raise Http404
    return idea, members


@transaction.atomic
def create(user, request_id, couple_id, preset):
    couple, members = current_couple(user, couple_id, lock=True)
    existing = DateIdea.objects.filter(pk=request_id).first()
    if existing:
        idea, _ = idea_for(user, request_id)
        if idea.creator_id != user.pk or idea.preset != preset or idea.couple_id != couple.pk:
            raise plans.PlanConflict("This request was used for a different idea.")
        return idea, False
    audience = [m.pk for m in members]
    # Release only stale-audience open slots; these records are never exposed.
    DateIdea.objects.filter(couple=couple, state="open").exclude(audience_membership_ids=audience).update(state="withdrawn")
    same = eligible(couple, members).filter(state="open", preset=preset).first()
    if same:
        return same, False  # Open their idea; no implicit interest mutation.
    if eligible(couple, members).filter(state="open", creator=user).exists():
        raise plans.PlanConflict("Withdraw your current idea before suggesting another.")
    return DateIdea.objects.create(id=request_id, couple=couple, creator=user,
        audience_membership_ids=audience, preset=preset, interested_by=[user.pk]), True


@transaction.atomic
def act(user, pk, request_id, expected_revision, action, values):
    idea, members = idea_for(user, pk, lock=True)
    intent = plans.payload(dict(action=action, expected_revision=expected_revision, values=values))
    receipt = idea.operations.filter(author=user, request_id=request_id).first()
    if receipt:
        if receipt.payload != intent:
            raise plans.PlanConflict("This request was used for another action.")
        return idea
    if idea.revision != expected_revision or idea.state != "open":
        raise plans.PlanConflict("This idea changed. Refresh and review it.")
    if action == "interest":
        if idea.creator_id == user.pk:
            raise ValidationError("You already suggested this idea.")
        interested = set(idea.interested_by)
        if values["interested"]:
            interested.add(user.pk)
        else:
            interested.discard(user.pk)
        idea.interested_by = sorted(interested)
    elif action == "withdraw":
        if idea.creator_id != user.pk:
            raise Http404
        idea.state = "withdrawn"
    elif action == "plan":
        if set(idea.interested_by) != {m.user_id for m in members}:
            raise plans.PlanConflict("Both partners need to be interested before choosing a time.")
        plan, _ = plans.create(user, request_id, idea.couple_id, **values)
        idea.plan = plan
        idea.state = "planned"
    else:
        raise ValidationError("Choose a supported action.")
    idea.revision += 1
    idea.save()
    DateIdeaOperation.objects.create(idea=idea, author=user, request_id=request_id, payload=intent)
    return idea
