import random
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound
from .models import Delivery, Event, Installation
from .provider import ProviderFailure, send
from .services import destination, enabled_for, installation_cutoff


def claim():
    with transaction.atomic():
        ready = Delivery.objects.select_for_update(skip_locked=True).filter(
            Q(state="pending") | Q(state="sending", lease_until__lte=timezone.now()),
            next_attempt_at__lte=timezone.now()).order_by("next_attempt_at", "pk").first()
        if ready is None:
            return None
        ready.lease = uuid.uuid4()
        ready.lease_until = timezone.now() + timedelta(minutes=2)
        ready.state = "sending"
        ready.attempts += 1
        ready.save(update_fields=["lease", "lease_until", "state", "attempts"])
        return ready.pk, ready.lease


def eligible(delivery):
    install, event = delivery.installation, delivery.event
    if (install.version != delivery.binding_version or install.user_id != event.recipient_id
            or not install.active or not install.permitted or install.session.revoked_at
            or install.updated_at < installation_cutoff()
            or install.session.expires_at <= timezone.now() or not enabled_for(event)):
        return False
    try:
        destination(event, event.recipient, install.session_id)
    except NotFound:
        return False
    if event.kind == "chat.message":
        from couple_chat.models import MutationHead
        if MutationHead.objects.filter(message_id=event.resource, actor_id=event.recipient_id, kind="receipt.read").exists():
            return False
    return True


def run_once(sender=send):
    if not settings.NOTIFICATIONS_ENABLED:
        return False
    claimed = claim()
    if not claimed:
        return False
    delivery_id, lease = claimed
    row = Delivery.objects.select_related("event__recipient", "event__room__couple", "installation__session").filter(pk=delivery_id).first()
    if row is None:
        # Account/relationship deletion may cascade after the lease commits.
        return True
    state, outcome, retry = "suppressed", "stale", None
    if eligible(row):
        # Reserve the sound budget per installation, serializing competing workers.
        with transaction.atomic():
            install = Installation.objects.select_for_update().filter(pk=row.installation_id).first()
            if install is None:
                return True
            audible = not install.last_alert_at or install.last_alert_at < timezone.now() - timedelta(seconds=30)
            if audible:
                install.last_alert_at = timezone.now()
                install.save(update_fields=["last_alert_at"])
        # No database lock is held across the provider network call.
        try:
            sender(row, audible=audible)
            state, outcome = "sent", "accepted"
        except ProviderFailure as error:
            outcome = error.code
            if error.code == "unregistered":
                Installation.objects.filter(pk=row.installation_id, version=row.binding_version).update(active=False)
                state = "suppressed"
            else:
                delay = max(error.retry_after, 300 if error.code == "configuration" else 2 ** min(row.attempts, 10))
                retry = timezone.now() + timedelta(seconds=delay + random.uniform(0, 10))
                state = "pending" if retry < row.event.expires_at and row.attempts < 20 else "suppressed"
    values = dict(state=state, outcome=outcome, lease=None, lease_until=None)
    if retry:
        values["next_attempt_at"] = retry
    Delivery.objects.filter(pk=delivery_id, lease=lease, state="sending").update(**values)
    return True


def cleanup():
    now = timezone.now()
    Delivery.objects.filter(state__in=["pending", "sending"], event__expires_at__lte=now).update(state="suppressed", outcome="expired")
    Event.objects.filter(created_at__lt=now - timedelta(days=30)).delete()
    # Erase target values promptly after session expiry; preserve binding proof/version to fence old callbacks.
    Installation.objects.filter(Q(session__revoked_at__isnull=False) | Q(session__expires_at__lte=now)
        | Q(updated_at__lt=installation_cutoff())).update(active=False, target="")
