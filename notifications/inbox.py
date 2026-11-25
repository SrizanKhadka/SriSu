"""Account activity history. Push expiry/permission is not inbox authorization."""
from datetime import timedelta
from uuid import UUID

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, Exists, OuterRef, CharField
from django.db.models.functions import Cast
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError

from social.services.sparks import SparkConflict
from .catalogue import POLICIES
from .models import Event
from .previews import FALLBACKS, display_copy, text

CONTRACT = "notification-inbox-1"
RETENTION_DAYS = 30
PAGE_SIZE = 25
NOTE_KINDS = ("moment.appreciated", "moment.replied")


def visible_events(user):
    """Filter before counting/paging. Even existence must respect current access."""
    from authentication.models import UserModel
    from social.models import CoupleMembershipModel, CoupleConnectionModel
    from social.services.moment_publication import current_couple
    from social.services.moment_service import blocked_user_ids, visible_notes
    allowed = Q(pk__in=[])
    for couple_id in CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values_list("couple_id", flat=True):
        try:
            couple, members = current_couple(user, couple_id)
        except Http404:
            continue
        allowed |= Q(couple=couple, audience_membership_ids=[m.pk for m in members]) & ~Q(kind__in=NOTE_KINDS)
    senders = UserModel.objects.filter(is_active=True).exclude(pk__in=blocked_user_ids(user)).values("phone_number")
    invitations = CoupleConnectionModel.objects.filter(receiver_number=user.phone_number, sender_number__in=senders)
    allowed |= Q(kind="invite.received", resource__in=invitations.annotate(key=Cast("pk", CharField())).values("key"))
    # The visitor replying to an appreciation is not a member of the receiving couple.
    notes = visible_notes(user).annotate(key=Cast("pk", CharField())).values("key")
    allowed |= Q(kind__in=NOTE_KINDS, resource__in=notes)
    rows = Event.objects.filter(recipient=user, kind__in=POLICIES,
        created_at__gte=timezone.now() - timedelta(days=RETENTION_DAYS)).filter(allowed)
    newer_status = Event.objects.filter(recipient=user, kind="status.changed", resource=OuterRef("resource")).filter(
        Q(created_at__gt=OuterRef("created_at")) | Q(created_at=OuterRef("created_at"), pk__gt=OuterRef("pk")))
    return rows.annotate(newer_status=Exists(newer_status)).exclude(kind="status.changed", newer_status=True)


def inbox_destination(event, user):
    """Open the current authorized feature, including history after push TTL expires."""
    from social.services import plans, challenges, sparks, date_ideas
    from social.models import CoupleConnectionModel, CoupleStatus
    from social.services.moment_service import eligible_moments
    from .domain import note_destination
    kind = event.kind
    if kind in NOTE_KINDS:
        return note_destination(event, user)
    if kind == "invite.received":
        if not CoupleConnectionModel.objects.filter(pk=event.resource, receiver_number=user.phone_number,
                connection_status="PENDING").exists():
            raise NotFound()
        return {"kind": "invitations"}
    if kind.startswith("plan."):
        value, _ = plans.plan_for(user, event.resource)
        if value.couple_id != event.couple_id or value.audience_membership_ids != event.audience_membership_ids:
            raise NotFound()
        return {"kind": "plan", "plan_id": value.pk, "couple_id": value.couple_id}
    if kind.startswith("spark."):
        value, _ = sparks.session_for(user, event.resource)
        if value.couple_id != event.couple_id or value.audience_membership_ids != event.audience_membership_ids:
            raise NotFound()
        return {"kind": "spark", "spark_id": str(value.pk)}
    if kind.startswith("challenge."):
        value = challenges.challenge_for(user, event.resource)
        if value.couple_id != event.couple_id or value.audience_membership_ids != event.audience_membership_ids:
            raise NotFound()
        return {"kind": "challenge", "challenge_id": str(value.pk)}
    if kind.startswith("idea."):
        value, _ = date_ideas.idea_for(user, event.resource)
        if value.couple_id != event.couple_id or value.audience_membership_ids != event.audience_membership_ids:
            raise NotFound()
        if value.state != "open":
            raise NotFound()
        return {"kind": "idea", "idea_id": str(value.pk)}
    if kind in ("moment.published", "moment.hearted"):
        value = eligible_moments(user).filter(pk=event.resource, couple_id=event.couple_id).first()
        if value is None:
            raise NotFound()
        return {"kind": "moment", "moment_id": value.pk, "couple_id": value.couple_id}
    if kind == "status.changed":
        if not CoupleStatus.objects.filter(pk=event.resource, couple_id=event.couple_id,
                audience_membership_ids=event.audience_membership_ids, expires_at__gt=timezone.now()).exclude(text="").exists():
            raise NotFound()
        return {"kind": "home"}
    if kind.startswith("sharing."):
        return {"kind": "sharing", "couple_id": event.couple_id}
    if kind == "invite.accepted":
        return {"kind": "couple", "couple_id": event.couple_id}
    raise NotFound()


def presentation(event, user):
    from .inbox_presentation import visuals
    route = None
    visual = {"actor_name": None, "avatar": False, "thumbnail": False, "segments": []}
    try:
        route = inbox_destination(event, user)
        copy = display_copy(event, user)
        # Historical first-answer copy must not claim it is still the reader's turn.
        if event.kind == "spark.submitted":
            copy["title"] = copy["title"].replace("answered — your turn", "answered your question")
        visual = visuals(event, user, copy)
    except (NotFound, Http404, ObjectDoesNotExist, SparkConflict):
        title, _ = FALLBACKS[event.kind]
        copy = {"title": title, "body": "This update is no longer available."}
        route = None
    return {"id": str(event.pk), "kind": event.kind, "category": POLICIES[event.kind].category,
        "title": text(copy["title"], 120), "body": text(copy["body"]),
        "created_at": event.created_at, "read_at": event.inbox_read_at,
        "available": route is not None, **visual}, route


def encode(user, purpose, **values):
    return signing.dumps({"user": user.pk, "purpose": purpose, **values}, salt=CONTRACT, compress=True)


def decode(user, token, purpose):
    try:
        if len(token) > 2048:
            raise ValueError()
        value = signing.loads(token, salt=CONTRACT, max_age=86400)
        if value["user"] != user.pk or value["purpose"] != purpose:
            raise ValueError()
        return value
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError("This notification page has expired. Refresh to continue.") from None


def summary(user):
    return {"contract": CONTRACT, "unread_count": visible_events(user).filter(inbox_read_at__isnull=True).count()}


def page(user, *, cursor=None, unread=False):
    now = timezone.now()
    rows = visible_events(user).filter(created_at__lte=now)
    if unread:
        rows = rows.filter(inbox_read_at__isnull=True)
    if cursor:
        value = decode(user, cursor, "page")
        try:
            at = parse_datetime(value["at"]); key = UUID(value["id"])
            if at is None or value["unread"] != unread:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValidationError("Refresh notifications before continuing.") from None
        rows = rows.filter(Q(created_at__lt=at) | Q(created_at=at, pk__lt=key))
    batch = list(rows.select_related("couple").order_by("-created_at", "-pk")[:PAGE_SIZE + 1])
    selected = batch[:PAGE_SIZE]
    next_cursor = encode(user, "page", at=selected[-1].created_at.isoformat(), id=str(selected[-1].pk), unread=unread) if len(batch) > PAGE_SIZE else None
    # Snapshot boundary prevents Mark all read from swallowing newly arriving events.
    return {**summary(user), "items": [presentation(e, user)[0] for e in selected],
        "next_cursor": next_cursor, "read_token": encode(user, "read", through=now.isoformat()),
        "server_time": now, "retention_days": RETENTION_DAYS}


@transaction.atomic
def mark_read(user, *, ids=None, through=None):
    rows = visible_events(user)
    if through:
        value = decode(user, through, "read")
        at = parse_datetime(value.get("through", ""))
        if at is None or at > timezone.now():
            raise ValidationError("Refresh notifications before marking them read.")
        rows = rows.filter(created_at__lte=at)
    else:
        rows = rows.filter(pk__in=ids)
        if rows.count() != len(set(ids)):
            raise NotFound()
    rows.filter(inbox_read_at__isnull=True).update(inbox_read_at=timezone.now())
    return summary(user)


@transaction.atomic
def open_item(user, event_id):
    event = visible_events(user).filter(pk=event_id).select_related("couple").first()
    if event is None:
        raise NotFound()
    item, route = presentation(event, user)
    Event.objects.filter(pk=event.pk, recipient=user, inbox_read_at__isnull=True).update(inbox_read_at=timezone.now())
    return {**summary(user), "destination": route, "available": route is not None}
