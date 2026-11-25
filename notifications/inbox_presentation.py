"""Transient inbox visuals, resolved only after current-resource authorization."""
from io import BytesIO

from django.http import Http404, HttpResponse
from django.core.exceptions import ObjectDoesNotExist
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import NotFound

from .previews import name


def actor_for(event, user):
    from authentication.models import UserModel
    from social.models import CoupleConnectionModel, CoupleMomentNoteModel
    if event.kind == "invite.received":
        invite = CoupleConnectionModel.objects.get(pk=event.resource)
        return UserModel.objects.filter(phone_number=invite.sender_number, is_active=True).first()
    if event.kind in ("moment.appreciated", "moment.replied"):
        note = CoupleMomentNoteModel.objects.select_related("sender").get(pk=event.resource)
        if event.kind == "moment.appreciated":
            return note.sender
        return note.replies.select_related("author").get(pk=event.context["reply_id"]).author
    if event.couple_id:
        member = event.couple.memberships.filter(ended_at__isnull=True).exclude(user=user).select_related("user").first()
        return member.user if member else None
    return None


def thumbnail_for(event):
    # Challenge proof is not implemented in the current domain. Never invent one.
    if event.kind in ("moment.published", "moment.hearted"):
        from social.models import CoupleMomentPhotoModel
        photo = CoupleMomentPhotoModel.objects.filter(moment_id=event.resource).order_by("order", "id").first()
        return photo.image if photo else None
    return None


def visuals(event, user, copy):
    actor = actor_for(event, user)
    actor_name = copy.get("actor_name") if actor else None
    actor_real_name = name(actor, "Someone") if actor else None
    title, body = copy["title"], copy["body"]
    segments = []
    def add(value, bold=False, person=False):
        if value:
            segment = {"text": value, "bold": bold}
            if person and actor:
                segment.update(user_id=actor.pk, user_name=actor_real_name)
            segments.append(segment)
    if event.kind == "spark.submitted":
        from social.models import SparkSession
        spark = SparkSession.objects.get(pk=event.resource)
        add(actor_name or "Your partner", True, person=True)
        add(" answered your Spark.")
        if spark.state == "open" and not spark.answers.filter(author=user).exists():
            add(" Answer to see theirs.")
        elif spark.state == "revealed":
            add(" Your answers are ready to reveal.")
    else:
        # Revealed-answer copy is a collective sentence, never an actor label,
        # even if the user chooses a nickname such as "Your".
        if event.kind != "spark.revealed" and actor_name and title.startswith(actor_name):
            add(actor_name, True, person=True)
            add(title[len(actor_name):])
        else:
            add(title, True)
        if event.kind.startswith("plan.") or event.kind.startswith("challenge."):
            add(" · ")
            subject, separator, details = body.partition(" · ")
            add(subject, True)
            add(separator + details)
        elif event.kind in ("status.changed", "idea.created", "idea.interested"):
            add(" · " + body)
        elif event.kind in ("moment.appreciated", "moment.replied"):
            add(" · " + body)
        # Moment captions and Spark questions belong in their destination. Keep
        # the list concise; original title/body stay available to older clients.
    return {"actor_id": actor.pk if actor else None, "actor_name": actor_name, "actor_real_name": actor_real_name, "avatar": bool(actor and actor.profile_photo),
            "thumbnail": bool(thumbnail_for(event)), "segments": segments}


def media(user, event_id, slot):
    from .inbox import visible_events, inbox_destination
    from social.services.sparks import SparkConflict
    if slot not in ("avatar", "thumbnail"):
        raise NotFound()
    event = visible_events(user).select_related("couple").filter(pk=event_id).first()
    if event is None:
        raise NotFound()
    try:
        inbox_destination(event, user)
    except (SparkConflict, ObjectDoesNotExist):
        raise NotFound() from None
    try:
        actor = actor_for(event, user) if slot == "avatar" else None
    except ObjectDoesNotExist:
        raise NotFound() from None
    field = actor.profile_photo if actor else thumbnail_for(event) if slot == "thumbnail" else None
    if not field:
        raise NotFound()
    try:
        with field.open("rb") as source, Image.open(source) as original:
            original.draft("RGB", (160, 160))
            image = ImageOps.exif_transpose(original)
            image.thumbnail((160, 160), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=82)
    except (FileNotFoundError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise Http404 from None
    response = HttpResponse(output.getvalue(), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
