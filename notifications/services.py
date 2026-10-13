import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework.exceptions import APIException, NotFound
from authentication.models import DeviceSession, UserModel
from couple_chat.services import available_rooms
from .models import Delivery, Event, Installation, Preferences


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
    if old:
        if not constant_time_compare(old.proof_digest, proof) or data["version"] < old.version:
            raise BindingConflict()
        if data["version"] == old.version:
            if (old.user_id != user.pk or old.session_id != session.pk or old.target_digest != target_hash
                    or old.permitted != data["permitted"] or not old.active):
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
        target_digest=target_hash, permitted=data["permitted"], active=True)
    if old:
        for key, value in values.items():
            setattr(old, key, value)
        old.save()
        Delivery.objects.filter(installation=old, state__in=["pending", "sending"]).update(state="suppressed", outcome="binding_changed")
        return old
    return Installation.objects.create(id=data["installation_id"], **values)


def enabled_for(event):
    pref = Preferences.objects.filter(user_id=event.recipient_id).first()
    if pref is None:
        return True
    if not pref.push:
        return False
    if event.kind == "chat.message":
        return pref.chat and not pref.mute_forever and (not pref.mute_until or pref.mute_until <= timezone.now())
    return pref.activity


def emit(kind, recipient, source, resource, *, room=None, device=None, change=None, version=0, expires_at=None):
    """Called inside the domain mutation transaction. Fanout captures current bindings only."""
    if not settings.NOTIFICATIONS_ENABLED:
        return None
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Notification events require a domain transaction")
    hours = {"chat.message": 1, "invite.received": 24, "sharing.proposed": 24}.get(kind, 6)
    expiry = timezone.now() + timedelta(hours=hours)
    if expires_at:
        expiry = min(expiry, expires_at)
    event, created = Event.objects.get_or_create(recipient=recipient, kind=kind, source=str(source), defaults={
        "resource": str(resource), "room": room, "device": device, "change": change,
        "resource_version": version, "expires_at": expiry})
    if not created or not enabled_for(event) or expiry <= timezone.now():
        return event
    installs = Installation.objects.filter(user=recipient, active=True, permitted=True,
        updated_at__gte=installation_cutoff(),
        session__revoked_at__isnull=True, session__expires_at__gt=timezone.now())
    if device:
        installs = installs.filter(session_id=device.session_id)
    for install in installs:
        if kind == "chat.message":
            # Two-second coalescing window, limited to the same encrypted room/binding.
            Delivery.objects.filter(installation=install, binding_version=install.version,
                event__room=room, event__kind=kind, state="pending").update(state="suppressed", outcome="coalesced")
        Delivery.objects.create(event=event, installation=install, binding_version=install.version,
            next_attempt_at=timezone.now() + timedelta(seconds=2))
    return event


def destination(event, user, sid):
    """Reauthorize at every attempt and tap. Return no historical relationship data."""
    if event.recipient_id != user.pk or event.expires_at <= timezone.now() or not user.is_active:
        raise NotFound()
    room = None
    if event.room_id:
        room = available_rooms(user).filter(pk=event.room_id).first()
        if room is None:
            raise NotFound()
    if event.device_id:
        from couple_chat.protocol import live_devices
        if not live_devices().filter(pk=event.device_id, session_id=sid, user=user).exists():
            raise NotFound()
    if event.kind in ("chat.message", "spark.revealed"):
        from couple_chat.models import Message, Spark, MutationHead
        if not room or not Message.objects.filter(pk=event.resource, room=room, deleted_at__isnull=True).exists():
            raise NotFound()
        if event.kind == "spark.revealed" and not Spark.objects.filter(pk=event.resource, revealed_at__isnull=False).exists():
            raise NotFound()
        return {"kind": "conversation", "room_id": str(room.pk), "message_id": event.resource}
    if event.kind == "invite.received":
        from social.models import CoupleConnectionModel
        from social.services.moment_service import blocked_user_ids
        from utils.choices import CoupleConnectionStatus
        invite = CoupleConnectionModel.objects.filter(pk=event.resource, receiver_number=user.phone_number,
            connection_status=CoupleConnectionStatus.PENDING).first()
        if not invite or not UserModel.objects.filter(phone_number=invite.sender_number, is_active=True).exclude(pk__in=blocked_user_ids(user)).exists():
            raise NotFound()
        return {"kind": "invitations"}
    if not room:
        raise NotFound()
    if event.kind in ("plan.created", "plan.responded"):
        from social.models import CouplePlanModel
        plan = CouplePlanModel.objects.filter(pk=event.resource, couple_id=room.couple_id,
            audience_membership_ids=sorted([room.first_membership_id, room.second_membership_id]),
            completed=False, starts_at__gt=timezone.now()).first()
        if not plan or (event.kind == "plan.responded" and plan.revision != event.resource_version):
            raise NotFound()
        return {"kind": "plan", "couple_id": room.couple_id, "plan_id": plan.pk}
    if event.kind == "sharing.proposed":
        from social.services.couple_profile_sections import raw_sections, digest as section_digest
        couple = room.couple
        if (couple.privacy_proposal_version != event.resource_version or not couple.privacy_proposal.get("sections")
                or couple.privacy_proposal.get("audience") != sorted([room.first_membership_id, room.second_membership_id])
                or couple.privacy_consents.filter(user=user, proposal_version=event.resource_version).exists()):
            raise NotFound()
        members = list(couple.memberships.select_related("user").order_by("position"))
        raw = raw_sections(couple, members, user)
        proposal = couple.privacy_proposal
        if any(section not in raw or proposal.get("fingerprints", {}).get(section) != section_digest(raw[section])
               for section in proposal["sections"]):
            raise NotFound()
        return {"kind": "sharing", "couple_id": room.couple_id}
    if event.kind == "invite.accepted":
        return {"kind": "couple", "couple_id": room.couple_id}
    raise NotFound()


def partner_event(kind, actor, couple, resource, source, **kwargs):
    room = available_rooms(actor).filter(couple=couple).first()
    if room:
        recipient = room.second if room.first_id == actor.pk else room.first
        return emit(kind, recipient, source, resource, room=room, **kwargs)
