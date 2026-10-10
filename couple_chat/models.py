import uuid

from django.conf import settings
from django.db import models


class Room(models.Model):
    """One room per relationship instance. Participants never change in place."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    couple = models.OneToOneField("social.CoupleModel", on_delete=models.CASCADE, related_name="private_chat")
    first = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="couple_chat_first")
    second = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="couple_chat_second")
    first_membership_id = models.PositiveBigIntegerField()
    second_membership_id = models.PositiveBigIntegerField()
    sequence = models.PositiveBigIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(first_id__lt=models.F("second_id")), name="cc_ordered_participants")]


class Change(models.Model):
    """Committed, ordered room changes; also the retryable dispatch outbox."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="changes")
    sequence = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=32)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["room", "sequence"], name="cc_unique_change_sequence")]
        indexes = [models.Index(fields=["dispatched_at", "id"], name="cc_dispatch_idx")]


class Device(models.Model):
    """One chat installation per account, bound to an authenticated login session.

    All key columns contain PUBLIC key material. Retired devices remain as routing
    tombstones; replacing a device never redirects its ciphertext to the new one.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_devices")
    session = models.ForeignKey("authentication.DeviceSession", on_delete=models.CASCADE)
    registration_id = models.PositiveIntegerField()
    identity_key = models.TextField()
    signed_prekey = models.JSONField()
    registration_digest = models.CharField(max_length=64)
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user"], condition=models.Q(revoked_at__isnull=True), name="cc_one_active_device")]


class PreKey(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="prekeys")
    kind = models.CharField(max_length=8, choices=[("ec", "EC"), ("kyber", "Kyber")])
    key_id = models.PositiveIntegerField()
    public_key = models.TextField()
    signature = models.TextField(blank=True)
    consumed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "kind", "key_id"], name="cc_unique_prekey")]
        indexes = [models.Index(fields=["device", "kind", "consumed_at"], name="cc_available_key_idx")]


class KeyPublication(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    operation_id = models.UUIDField()
    digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "operation_id"], name="cc_unique_publication")]


class BundleClaim(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    requester = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="bundle_claims")
    recipient = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="claimed_bundles")
    operation_id = models.UUIDField()
    # Immutable public snapshot. A retry cannot burn another one-time prekey.
    bundle = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["room", "requester", "operation_id"], name="cc_unique_bundle_claim")]


class Message(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    reply_to = models.ForeignKey("self", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True)


class Operation(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    operation_id = models.UUIDField()
    change = models.OneToOneField(Change, on_delete=models.CASCADE, related_name="operation")
    sender_device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="sent_operations")
    recipient_device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="received_operations")
    kind = models.CharField(max_length=24)
    target_id = models.UUIDField()
    version = models.PositiveBigIntegerField(default=0)
    applied = models.BooleanField(default=True)
    signal_type = models.PositiveSmallIntegerField()
    ciphertext = models.TextField()
    attachment_ids = models.JSONField(default=list)
    digest = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["room", "actor", "operation_id"], name="cc_unique_operation")]


class MutationHead(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    kind = models.CharField(max_length=24)
    version = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "actor", "kind"], name="cc_unique_mutation_head")]


class Spark(models.Model):
    """A reveal gate only. The question/deck remain in the encrypted message."""
    message = models.OneToOneField(Message, primary_key=True, on_delete=models.CASCADE)
    first_device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="first_sparks")
    second_device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="second_sparks")
    revealed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SparkAnswer(models.Model):
    spark = models.ForeignKey(Spark, on_delete=models.CASCADE, related_name="answers")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    operation_id = models.UUIDField()
    envelope = models.JSONField()
    digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["spark", "actor"], name="cc_one_spark_answer")]


class Attachment(models.Model):
    """Opaque, immutable ciphertext. Keys, MIME types and captions stay on clients."""
    id = models.UUIDField(primary_key=True, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    intended_message_id = models.UUIDField()
    message = models.ForeignKey(Message, null=True, on_delete=models.SET_NULL, related_name="attachments")
    ciphertext_size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    blob = models.FileField(max_length=240, blank=True)
    uploaded_at = models.DateTimeField(null=True)
    ready_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["room", "deleted_at"], name="cc_media_quota_idx"),
                   models.Index(fields=["message", "expires_at"], name="cc_media_orphan_idx")]


class FileCleanup(models.Model):
    """Retryable storage deletion, including after account/room cascades."""
    name = models.CharField(max_length=240, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
