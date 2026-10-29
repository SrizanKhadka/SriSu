import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from authentication.models import UserModel
from social.models import CoupleModel


class ChatRoom(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user_one = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms_as_user_one",
        null=True,
        blank=True,
    )
    user_two = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms_as_user_two",
        null=True,
        blank=True,
    )

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Chat Room"
        verbose_name_plural = "Chat Rooms"
        constraints = [
            models.UniqueConstraint(
                fields=["couple"],
                condition=models.Q(couple__isnull=False),
                name="unique_chatroom_couple",
            )
        ]
        indexes = [
            models.Index(fields=["updated_at"]),
            models.Index(fields=["user_one", "user_two"], name="chat_room_pair_idx"),
        ]

    def clean(self):
        if self.user_one_id and self.user_two_id and self.user_one_id == self.user_two_id:
            raise ValidationError("user_one and user_two cannot be the same user.")

    def save(self, *args, **kwargs):
        if self.user_one_id and self.user_two_id and self.user_one_id > self.user_two_id:
            self.user_one, self.user_two = self.user_two, self.user_one
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def participants(self):
        return [user for user in [self.user_one, self.user_two] if user]

    def __str__(self):
        user_one_name = getattr(self.user_one, "full_name", "Unknown")
        user_two_name = getattr(self.user_two, "full_name", "Unknown")
        return f"ChatRoom {self.id} - {user_one_name} and {user_two_name}"


class MatrixUserMapping(models.Model):
    """Stable Django-account to private-Matrix identity mapping."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Failed"

    user = models.OneToOneField(
        UserModel,
        on_delete=models.CASCADE,
        related_name="matrix_identity",
    )
    matrix_localpart = models.CharField(max_length=64, unique=True, editable=False)
    matrix_user_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    last_error_code = models.CharField(max_length=40, blank=True, editable=False)
    provisioned_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MatrixRoomMapping(models.Model):
    """Relationship-room provisioning state; contains no message content."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        PROVISIONING = "provisioning", "Provisioning"
        ACTIVE = "active", "Active"
        REVOKE_PENDING = "revoke_pending", "Revoke pending"
        REVOKING = "revoking", "Revoking"
        REVOKED = "revoked", "Revoked"
        FAILED = "failed", "Failed"

    chat_room = models.OneToOneField(
        ChatRoom,
        on_delete=models.SET_NULL,
        related_name="matrix_mapping",
        null=True,
    )
    member_matrix_localparts = models.JSONField(default=list, editable=False)
    pending_remote_revocations = models.JSONField(default=list, editable=False)
    replacement_pending = models.BooleanField(default=False, editable=False)
    membership_epoch = models.PositiveBigIntegerField(default=1, editable=False)
    room_alias_localpart = models.CharField(max_length=128, unique=True, editable=False)
    matrix_room_alias = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    matrix_room_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveIntegerField(default=0, editable=False)
    available_at = models.DateTimeField(default=timezone.now, editable=False)
    locked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error_code = models.CharField(max_length=40, blank=True, editable=False)
    provisioned_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    remote_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["state", "available_at", "id"],
                name="matrix_room_retry_idx",
            )
        ]


class MatrixCutoverAttestation(models.Model):
    """Short-lived proof that every eligible room was verified remotely."""

    scope = models.CharField(max_length=40, primary_key=True, editable=False)
    configuration_digest = models.CharField(max_length=64, editable=False)
    state_digest = models.CharField(max_length=64, editable=False)
    eligible_room_count = models.PositiveIntegerField(editable=False)
    active_room_count = models.PositiveIntegerField(editable=False)
    verified_at = models.DateTimeField(editable=False)
    expires_at = models.DateTimeField(editable=False)

    class Meta:
        verbose_name = "Matrix cutover attestation"
