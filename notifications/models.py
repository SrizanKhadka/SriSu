"""Private routing metadata only. Never store message text or provider responses."""
import uuid
from django.conf import settings
from django.db import models


class Installation(models.Model):
    id = models.UUIDField(primary_key=True)
    proof_digest = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session = models.ForeignKey("authentication.DeviceSession", on_delete=models.CASCADE)
    version = models.PositiveBigIntegerField()
    platform = models.CharField(max_length=7)
    target_kind = models.CharField(max_length=5)
    target = models.CharField(max_length=4096)
    target_digest = models.CharField(max_length=64, unique=True)
    permitted = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_alert_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session"], condition=models.Q(active=True), name="notification_active_session")]


class Preferences(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    push = models.BooleanField(default=True)
    chat = models.BooleanField(default=True)
    activity = models.BooleanField(default=True)
    mute_until = models.DateTimeField(null=True)
    mute_forever = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=1)


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=24)
    source = models.CharField(max_length=160)
    couple = models.ForeignKey("social.CoupleModel", null=True, on_delete=models.CASCADE)
    audience_membership_ids = models.JSONField(default=list)
    # Historical FKs are retained for applied migration compatibility only.
    room = models.ForeignKey("couple_chat.Room", null=True, on_delete=models.CASCADE)
    device = models.ForeignKey("couple_chat.Device", null=True, on_delete=models.CASCADE)
    change = models.ForeignKey("couple_chat.Change", null=True, on_delete=models.SET_NULL)
    resource = models.CharField(max_length=64)
    resource_version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["recipient", "kind", "source"], name="notification_source_once")]


class Delivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.CASCADE)
    binding_version = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    outcome = models.CharField(max_length=24, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "installation", "binding_version"], name="notification_delivery_once")]
        indexes = [models.Index(fields=["state", "next_attempt_at"], name="notification_ready")]


class PlanCard(models.Model):
    """One explicit, authorized plan/card correlation; never inspect ciphertext."""
    plan = models.OneToOneField("social.CouplePlanModel", on_delete=models.CASCADE, primary_key=True)
    message = models.OneToOneField("couple_chat.Message", on_delete=models.CASCADE)
