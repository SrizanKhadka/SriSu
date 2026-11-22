"""A narrow profile projection for exact-phone discovery and current invitations."""
from django.core import signing
from django.db.models import Q
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import NotFound

from authentication.models import UserModel
from social.models import CoupleConnectionModel
from social.services.moment_service import active_couples, blocked_user_ids

PROFILE_ACCESS_MAX_AGE = 30 * 60
PROFILE_ACCESS_SALT = "srisu.partner-profile.v1"


def profile_access_token(viewer, target):
    return signing.dumps({"viewer": viewer.pk, "target": target.pk,
        "phone": salted_hmac(PROFILE_ACCESS_SALT, target.phone_number).hexdigest()},
        salt=PROFILE_ACCESS_SALT)


def _has_discovery_access(viewer, target, token):
    try:
        grant = signing.loads(token or "", salt=PROFILE_ACCESS_SALT, max_age=PROFILE_ACCESS_MAX_AGE)
        return grant == {"viewer": viewer.pk, "target": target.pk,
            "phone": salted_hmac(PROFILE_ACCESS_SALT, target.phone_number).hexdigest()}
    except (signing.BadSignature, TypeError, ValueError):
        return False


def partner_profile(viewer, target_id, token):
    target = UserModel.objects.filter(pk=target_id, is_active=True).exclude(pk__in=blocked_user_ids(viewer)).first()
    if target is None:
        raise NotFound("This profile is unavailable.")
    pair = CoupleConnectionModel.objects.filter(
        Q(sender_number=viewer.phone_number, receiver_number=target.phone_number) |
        Q(sender_number=target.phone_number, receiver_number=viewer.phone_number),
        connection_status__in=["PENDING", "ACCEPTED"], ended_at__isnull=True,
    ).order_by("-updated_at", "-id").first()
    if pair and pair.connection_status == "ACCEPTED" and not active_couples().filter(
        couple_connection=pair,
    ).filter(memberships__user=viewer, memberships__ended_at__isnull=True).filter(
        memberships__user=target, memberships__ended_at__isnull=True,
    ).exists():
        pair = None
    if viewer.pk != target.pk and pair is None and not _has_discovery_access(viewer, target, token):
        raise NotFound("This profile is unavailable. Search for the person again.")

    state = "available"
    if viewer.pk == target.pk:
        state = "self"
    elif pair:
        state = "connected" if pair.connection_status == "ACCEPTED" else (
            "outgoing" if pair.sender_number == viewer.phone_number else "incoming")
    elif CoupleConnectionModel.objects.filter(
        Q(sender_number__in=[viewer.phone_number, target.phone_number]) |
        Q(receiver_number__in=[viewer.phone_number, target.phone_number]),
        connection_status__in=["PENDING", "ACCEPTED"], ended_at__isnull=True,
    ).exists():
        state = "unavailable"
    # Sharing is opt-in before linking. A block, breakup or opt-out takes effect
    # on the next read; a signed search grant never overrides this decision.
    visible = target.share_partner_details or state in ["self", "connected"]
    return target, visible, state, pair.pk if pair else None


def serialize_partner_profile(request, target, visible, state, request_id):
    user = {"id": target.pk, "full_name": target.full_name, "username": target.username,
        "phone_number": target.phone_number,
        "profile_photo": request.build_absolute_uri(target.profile_photo.url) if target.profile_photo else None}
    if visible:
        user["bio"] = target.bio
        user["user_interests"] = list(target.user_interests.filter(removed=False).exclude(name="").values("id", "name"))
    # No album URLs, location, birthday, email, couple data or model-wide serializers.
    return {"user": user, "details_visible": visible, "connection_state": state, "request_id": request_id}
