"""Authorized, transient display copy. Private previews never enter FCM or the outbox."""
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.utils.html import strip_tags
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound
from .services import destination, enabled_for

CAPABILITY = "notification-previews-1"

# Safe copy for legacy clients, locked screens and unavailable/offline previews.
FALLBACKS = {
    "matrix.message": ("New message", "Open your chat to read the message."),
    "chat.message": ("New message", "Open your chat to read the message."),
    "invite.received": ("Partner invitation", "Someone invited you to connect on SriSu."),
    "invite.accepted": ("You're connected", "Your partner accepted your invitation."),
    "sharing.proposed": ("Sharing request", "Your partner proposed a change to what you share."),
    "sharing.approved": ("Sharing approved", "Your partner approved your sharing settings."),
    "plan.created": ("Plan invitation", "Your partner invited you to a plan."),
    "plan.responded": ("Plan response", "Your partner responded to your plan."),
    "plan.cancelled": ("Plan cancelled", "Your partner cancelled a plan."),
    "spark.started": ("A question for you", "Your partner started a question for you both."),
    "spark.revealed": ("Answers ready", "You have both answered. Your answers are ready to reveal."),
    "spark.submitted": ("Your turn to answer", "Your partner answered. Add yours to reveal both answers."),
    "challenge.invited": ("Challenge invitation", "Your partner invited you to a challenge."),
    "challenge.responded": ("Challenge response", "Your partner responded to your challenge."),
    "challenge.completed": ("Challenge complete", "You both completed your challenge."),
    "challenge.cancelled": ("Challenge cancelled", "Your partner cancelled a challenge."),
    "challenge.progress": ("Challenge progress", "Your partner updated your challenge."),
    "moment.published": ("New moment", "Your partner shared a new moment."),
    "moment.hearted": ("A heart for your moment", "Your partner liked your moment."),
    "moment.appreciated": ("New appreciation", "Someone sent a note about your moment."),
    "moment.replied": ("Reply to your appreciation", "You received a reply to your note."),
    "status.changed": ("Partner status", "Your partner updated their status."),
    "idea.created": ("New date idea", "Your partner suggested something to do together."),
    "idea.interested": ("It's a mutual idea", "You're both interested. Choose a time together."),
}


def text(value, limit=240):
    value = strip_tags(str(value or ""))
    value = "".join(c if c.isspace() or (unicodedata.category(c) not in {"Cc", "Cs"}
        and c not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069") else "" for c in value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def name(user, fallback="Your partner"):
    return text(getattr(user, "full_name", ""), 60) or fallback


def preview(event, user, session_id):
    # The same current-resource/membership fence as a notification tap, before
    # loading any private content. Re-check preferences for queued fetches too.
    destination(event, user, session_id)
    if not enabled_for(event):
        raise NotFound()
    return display_copy(event, user)


def display_copy(event, user):
    """Private transient copy: caller MUST authorize the current resource first."""
    title, body = FALLBACKS.get(event.kind, ("SriSu", "Open SriSu to see what's new."))
    partner = event.couple.memberships.filter(ended_at__isnull=True).exclude(user=user).select_related("user").first() if event.couple_id else None
    actor = name(partner.user if partner else None)
    result = {"event_kind": event.kind, "title": title, "body": body}
    kind = event.kind
    if kind == "matrix.message":
        # Ciphertext and plaintext stay in Matrix. The receiving device resolves
        # this exact event using its existing encrypted store and session.
        result.update(title=actor, room_id=event.resource, matrix_event_id=event.matrix_event_id)
    elif kind == "invite.received":
        from authentication.models import UserModel
        from social.models import CoupleConnectionModel
        invite = CoupleConnectionModel.objects.get(pk=event.resource)
        actor = name(UserModel.objects.filter(phone_number=invite.sender_number).first(), "Someone")
        result.update(title=f"{actor} invited you to connect", body="Accept the invitation to start your shared space.")
    elif kind == "invite.accepted":
        result.update(title=f"{actor} accepted your invitation", body="You're connected. Open your shared space.")
    elif kind.startswith("sharing."):
        result.update(title=f"{actor} " + ("proposed a sharing change" if kind == "sharing.proposed" else "approved sharing"))
    elif kind.startswith("plan."):
        from social.models import CouplePlanModel
        plan = CouplePlanModel.objects.get(pk=event.resource)
        action = event.context.get("action")
        verb = "invited you to a plan" if kind == "plan.created" else "cancelled your plan" if kind == "plan.cancelled" else {
            "accept": "accepted your plan", "decline": "declined your plan", "propose": "suggested a plan change",
            "withdraw_change": "withdrew a plan change"}.get(action, {"yes": "accepted your plan", "no": "declined your plan",
                "another_time": "suggested another time"}.get(plan.response, "updated your plan"))
        change = plan.pending_change if action == "propose" else {}
        starts = parse_datetime(change["starts_at"]) if change else plan.starts_at
        zone = change.get("time_zone", plan.time_zone) or "UTC"
        try:
            starts = starts.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            pass
        details = [change.get("title", plan.title), starts.strftime("%a, %d %b · %H:%M") + f" {zone}",
            change.get("place", plan.place)]
        result.update(title=f"{actor} {verb}", body=" · ".join(text(v) for v in details if v))
    elif kind.startswith("spark."):
        from social.models import SparkSession
        spark = SparkSession.objects.get(pk=event.resource)
        result.update(title={"spark.started": f"{actor} has a question for you", "spark.submitted": f"{actor} answered — your turn",
            "spark.revealed": "Your answers are ready"}[kind], body=spark.question)
    elif kind.startswith("challenge."):
        from social.models import CoupleChallenge
        challenge = CoupleChallenge.objects.get(pk=event.resource)
        verb = {"challenge.invited": "invited you to a challenge", "challenge.completed": "completed your challenge with you",
            "challenge.cancelled": "cancelled your challenge", "challenge.responded": "declined your challenge" if challenge.state == "declined" else "accepted your challenge",
            "challenge.progress": {"confirm": "finished their part of your challenge", "pause": "paused their part of your challenge",
                "resume": "resumed their part of your challenge"}.get(event.context.get("action"),
                    "paused your challenge" if challenge.state == "paused" else "updated your challenge")}[kind]
        result.update(title=f"{actor} {verb}", body=challenge.snapshot.get("title") or title)
    elif kind in {"moment.published", "moment.hearted"}:
        from social.models import CoupleMomentModel
        moment = CoupleMomentModel.objects.get(pk=event.resource)
        result.update(title=f"{actor} " + ("shared a moment" if kind == "moment.published" else "liked your moment"), body=moment.caption or moment.title or "Photo moment")
    elif kind in {"moment.appreciated", "moment.replied"}:
        from social.models import CoupleMomentNoteModel
        note = CoupleMomentNoteModel.objects.select_related("sender").get(pk=event.resource)
        if kind == "moment.appreciated":
            result.update(title=f"{name(note.sender, 'Someone')} appreciated your moment", body=note.message)
        else:
            reply = note.replies.select_related("author").get(pk=event.context["reply_id"])
            result.update(title=f"{name(reply.author, 'Someone')} replied to your note", body=reply.message)
    elif kind == "status.changed":
        from social.models import CoupleStatus
        status = CoupleStatus.objects.get(pk=event.resource)
        result.update(title=f"{actor} updated their status", body=status.text)
    elif kind.startswith("idea."):
        from social.models import DateIdea
        idea = DateIdea.objects.get(pk=event.resource)
        result.update(title=f"{actor} " + ("suggested a date" if kind == "idea.created" else "likes your date idea"),
            body={"coffee": "Coffee together", "walk": "A walk together", "movie": "Movie night"}.get(idea.preset, "A date together"))
    result["title"] = text(result["title"], 120)
    result["body"] = text(result["body"])
    return result
