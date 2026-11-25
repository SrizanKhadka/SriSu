import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework.exceptions import APIException, NotFound
from authentication.models import DeviceSession, UserModel
from django.http import Http404
from social.services.moment_publication import current_couple
from .models import Delivery, Event, Installation, Preferences
from .catalogue import CAPABILITY, POLICIES, categories, policy_expiry


class BindingConflict(APIException):
    status_code = 409
    default_detail = "Registration changed. Reconcile this installation."
    default_code = "notification_conflict"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def installation_cutoff():
    return timezone.now() - timedelta(days=max(1, settings.NOTIFICATION_INSTALLATION_MAX_AGE_DAYS))


@transaction.atomic
def register(user, sid, data):
    # Session locks serialize logout/revocation and registrations for one login.
    session = DeviceSession.objects.select_for_update().filter(pk=sid, user=user,
        revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
    if session is None:
        raise NotFound()
    old = Installation.objects.select_for_update().filter(pk=data["installation_id"]).first()
    proof = digest(data["proof"])
    target_hash = digest(data["target_kind"] + ":" + data["target"])
    capabilities = data.get("capabilities", [])
    if old:
        if not constant_time_compare(old.proof_digest, proof) or data["version"] < old.version:
            raise BindingConflict()
        if data["version"] == old.version:
            if (old.user_id != user.pk or old.session_id != session.pk or old.target_digest != target_hash
                    or old.permitted != data["permitted"] or old.capabilities != capabilities or not old.active):
                raise BindingConflict()
            old.save(update_fields=["updated_at"])
            return old
    if Installation.objects.filter(target_digest=target_hash).exclude(pk=data["installation_id"]).exists():
        raise BindingConflict()
    # A client cannot fill the table with unbounded target registrations.
    if old is None and Installation.objects.filter(session=session).count() >= 10:
        raise BindingConflict()
    Installation.objects.filter(session=session, active=True).exclude(pk=data["installation_id"]).update(active=False)
    values = dict(user=user, session=session, proof_digest=proof, version=data["version"],
        platform=data["platform"], target_kind=data["target_kind"], target=data["target"],
        target_digest=target_hash, permitted=data["permitted"], capabilities=capabilities, active=True)
    if old:
        for key, value in values.items():
            setattr(old, key, value)
        old.save()
        Delivery.objects.filter(installation=old, state__in=["pending", "sending"]).update(state="suppressed", outcome="binding_changed")
        return old
    return Installation.objects.create(id=data["installation_id"], **values)


def enabled_for(event):
    pref = Preferences.objects.filter(user_id=event.recipient_id).first()
    if pref is not None and not pref.push:
        return False
    if event.kind in {"chat.message", "matrix.message"}:
        return pref is None or (pref.chat and not pref.mute_forever and (not pref.mute_until or pref.mute_until <= timezone.now()))
    policy = POLICIES.get(event.kind)
    return bool(policy and (pref is None or pref.activity) and categories(pref)[policy.category])


ACTIVITY_KINDS = set(POLICIES)


def emit(kind, recipient, source, resource, *, couple=None, audience=(), version=0, expires_at=None, context=None, quiet=None):
    """Capture current bindings in the domain transaction; never backfill or inspect Matrix."""
    if kind not in ACTIVITY_KINDS:
        return None
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Notification events require a domain transaction")
    policy = POLICIES[kind]
    expiry = policy_expiry(kind, timezone.now())
    if expires_at:
        expiry = min(expiry, expires_at)
    event, created = Event.objects.get_or_create(recipient=recipient, kind=kind, source=str(source), defaults={
        "resource": str(resource), "couple": couple, "audience_membership_ids": list(audience),
        "resource_version": version, "expires_at": expiry, "context": context or {},
        "quiet": not policy.audible if quiet is None else quiet})
    if not created or not settings.NOTIFICATIONS_ENABLED or not enabled_for(event) or expiry <= timezone.now():
        return event
    installs = Installation.objects.filter(user=recipient, active=True, permitted=True,
        updated_at__gte=installation_cutoff(),
        session__revoked_at__isnull=True, session__expires_at__gt=timezone.now())
    for install in installs:
        if policy.modern and (install.platform != "android" or CAPABILITY not in install.capabilities):
            continue
        Delivery.objects.create(event=event, installation=install, binding_version=install.version,
            next_attempt_at=timezone.now() + timedelta(seconds=2))
    return event


def destination(event, user, sid):
    """Reauthorize current membership and resource state at every attempt and tap."""
    if event.recipient_id != user.pk or event.expires_at <= timezone.now() or not user.is_active:
        raise NotFound()
    if event.kind == "matrix.message":
        from .matrix_push import chat_destination
        return chat_destination(event, user)
    if event.kind in ("moment.appreciated", "moment.replied"):
        from .domain import note_destination
        return note_destination(event, user)
    if event.kind == "invite.received":
        from social.models import CoupleConnectionModel
        from social.services.moment_service import blocked_user_ids
        from utils.choices import CoupleConnectionStatus
        invite = CoupleConnectionModel.objects.filter(pk=event.resource, receiver_number=user.phone_number,
            connection_status=CoupleConnectionStatus.PENDING).first()
        if not invite or not UserModel.objects.filter(phone_number=invite.sender_number, is_active=True).exclude(pk__in=blocked_user_ids(user)).exists():
            raise NotFound()
        return {"kind": "invitations"}
    # Old chat/Spark deliveries are not translated into a different Matrix audience.
    if event.kind not in ACTIVITY_KINDS or not event.couple_id:
        raise NotFound()
    try:
        couple, members = current_couple(user, event.couple_id)
    except Http404:
        raise NotFound() from None
    audience = [m.pk for m in members]
    if audience != event.audience_membership_ids:
        raise NotFound()
    if event.kind == "nickname.changed":
        from .domain import nickname_member
        nickname_member(event, user, members=members)
        return {"kind": "couple", "couple_id": couple.pk}
    if event.kind.startswith("plan."):
        from .domain import plan_destination
        return plan_destination(event, couple, audience)
    if event.kind.startswith("spark."):
        from social.services.sparks import session_for, SparkConflict
        try:
            spark, _ = session_for(user, event.resource)
        except (Http404, SparkConflict):
            raise NotFound() from None
        if spark.couple_id != couple.pk or (spark.state != "revealed" if event.kind == "spark.revealed" else spark.state != "open"):
            raise NotFound()
        if event.kind in ("spark.started", "spark.submitted") and spark.answers.filter(author=user).exists():
            raise NotFound()
        return {"kind": "spark", "spark_id": str(spark.pk)}
    if event.kind in ("sharing.proposed", "sharing.approved"):
        from social.services.couple_profile_sections import raw_sections, digest as section_digest
        if (couple.privacy_proposal_version != event.resource_version or not couple.privacy_proposal.get("sections")
                or couple.privacy_proposal.get("audience") != audience
                or (event.kind == "sharing.proposed" and couple.privacy_consents.filter(user=user, proposal_version=event.resource_version).exists())
                or (event.kind == "sharing.approved" and couple.privacy_consents.filter(proposal_version=event.resource_version).count() != 2)):
            raise NotFound()
        people = list(couple.memberships.filter(ended_at__isnull=True).select_related("user").order_by("position"))
        raw = raw_sections(couple, people, user)
        proposal = couple.privacy_proposal
        if any(section not in raw or proposal.get("fingerprints", {}).get(section) != section_digest(raw[section])
               for section in proposal["sections"]):
            raise NotFound()
        return {"kind": "sharing", "couple_id": couple.pk}
    if event.kind == "invite.accepted":
        return {"kind": "couple", "couple_id": couple.pk}
    from .domain import extended_destination
    try:
        return extended_destination(event, user, couple, audience)
    except Http404:
        raise NotFound() from None


def partner_event(kind, actor, couple, resource, source, **kwargs):
    try:
        couple, members = current_couple(actor, couple.pk)
    except Http404:
        return None
    recipient = next((m.user for m in members if m.user_id != actor.pk), None)
    if recipient is not None:
        return emit(kind, recipient, source, resource, couple=couple, audience=[m.pk for m in members], **kwargs)
