"""Private activities. Locks serialize progress; receipts make retries safe."""
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException
from social.models import CoupleChallenge, ChallengeTemplate, ChallengeOperation
from social.services.moment_publication import current_couple

LIVE = ("invited", "active", "paused")
COLLECTIONS = [("gestures", "Little gestures", "Small things, thoughtfully done"),
    ("time", "Quality time", "Make a little room for each other"),
    ("play", "Play together", "Leave space for laughter"),
    ("new", "Try something new", "A first, shared with you")]
ACTIONS = ("accept", "decline", "confirm", "unconfirm", "pause", "resume", "cancel")


class ChallengeConflict(APIException):
    status_code = 409
    default_code = "challenge_changed"
    default_detail = "This Challenge changed. Refresh before trying again."


def catalogue():
    templates = list(ChallengeTemplate.objects.filter(published=True))
    return [{"id": key, "name": name, "subtitle": subtitle,
        "activities": [t.content for t in templates if t.collection == key]} for key, name, subtitle in COLLECTIONS]


def eligible(couple, members):
    return CoupleChallenge.objects.filter(couple=couple,
        audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members])


def challenge_for(user, pk, *, lock=False):
    initial = CoupleChallenge.objects.filter(pk=pk).first()
    if not initial or user.pk not in initial.audience_user_ids:
        raise Http404
    couple, members = current_couple(user, initial.couple_id, lock=lock)
    instance = eligible(couple, members).filter(pk=pk)
    challenge = (instance.select_for_update() if lock else instance).first()
    if not challenge:
        raise Http404
    return challenge


@transaction.atomic
def start(user, request_id, couple_id, template_id):
    couple, members = current_couple(user, couple_id, lock=True)
    existing = CoupleChallenge.objects.filter(pk=request_id).first()
    if existing:
        challenge = challenge_for(user, request_id)
        if challenge.creator_id != user.pk or challenge.couple_id != couple_id or challenge.template_id != template_id:
            raise ChallengeConflict("That invitation request was already used.")
        return challenge, False
    template = ChallengeTemplate.objects.filter(pk=template_id, published=True).first()
    if not template:
        raise ChallengeConflict("This activity is no longer available. Choose another one.")
    current = eligible(couple, members)
    if current.filter(state__in=LIVE).exists():
        raise ChallengeConflict("You already have a Challenge together. Open it to continue or end it first.")
    # A replaced audience cannot continue an earlier relationship's activity.
    CoupleChallenge.objects.filter(couple=couple, state__in=LIVE).exclude(pk__in=current.values("pk")).update(state="cancelled")
    return CoupleChallenge.objects.create(id=request_id, couple=couple, creator=user,
        template=template, snapshot=template.content,
        audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members],
        accepted_by=[user.pk]), True


@transaction.atomic
def act(user, pk, request_id, action, expected_revision):
    challenge = challenge_for(user, pk, lock=True)
    existing = challenge.operations.filter(author=user, request_id=request_id).first()
    if existing:
        if existing.action != action or existing.expected_revision != expected_revision:
            raise ChallengeConflict("That request was already used for a different action.")
        return challenge  # Return CURRENT progress, never resurrect an old pause.
    if challenge.revision != expected_revision:
        raise ChallengeConflict()
    if action not in ACTIONS:
        raise ChallengeConflict()
    if action in ("accept", "decline"):
        if challenge.state != "invited" or user.pk == challenge.creator_id:
            raise ChallengeConflict("Only the invited partner can respond to this invitation.")
        if action == "accept":
            challenge.accepted_by = sorted(challenge.audience_user_ids)
            challenge.state = "active"
        else:
            challenge.state = "declined"
    elif action == "cancel":
        if challenge.state not in LIVE:
            raise ChallengeConflict("This Challenge has already ended.")
        if challenge.state == "invited" and user.pk != challenge.creator_id:
            raise ChallengeConflict("Choose Not this time to decline this invitation.")
        challenge.state = "cancelled"
    else:
        if challenge.state not in ("active", "paused") or set(challenge.accepted_by) != set(challenge.audience_user_ids):
            raise ChallengeConflict("Both partners need to accept before making progress.")
        if action in ("confirm", "unconfirm"):
            if challenge.state == "paused":
                raise ChallengeConflict("Resume this Challenge before confirming progress.")
            confirmed = set(challenge.confirmed_by)
            (confirmed.add if action == "confirm" else confirmed.discard)(user.pk)
            challenge.confirmed_by = sorted(confirmed)
            if confirmed == set(challenge.audience_user_ids):
                challenge.state = "completed"
                challenge.completed_at = timezone.now()
        else:
            paused = set(challenge.paused_by)
            (paused.add if action == "pause" else paused.discard)(user.pk)
            challenge.paused_by = sorted(paused)
            challenge.state = "paused" if paused else "active"
    challenge.revision += 1
    challenge.save(update_fields=["state", "accepted_by", "confirmed_by", "paused_by", "completed_at", "revision"])
    ChallengeOperation.objects.create(challenge=challenge, author=user, request_id=request_id,
        action=action, expected_revision=expected_revision)
    return challenge


def data(challenge):
    # Matrix availability affects the optional chat card, not the activity itself.
    from chat.models import ChatRoom, MatrixRoomMapping
    room = ChatRoom.objects.filter(couple_id=challenge.couple_id).first()
    mapping = MatrixRoomMapping.objects.filter(chat_room=room, state="active", replacement_pending=False).first() if room else None
    return {"id": str(challenge.pk), "couple": challenge.couple_id, "creator": challenge.creator_id,
        "activity": challenge.snapshot, "state": challenge.state, "revision": challenge.revision,
        "accepted_by": challenge.accepted_by, "confirmed_by": challenge.confirmed_by, "paused_by": challenge.paused_by,
        "created_at": challenge.created_at, "completed_at": challenge.completed_at,
        "room_id": str(room.pk) if mapping else None, "matrix_room_id": mapping.matrix_room_id if mapping else None,
        "membership_epoch": mapping.membership_epoch if mapping else None, "card_event_id": challenge.card_event_id}
