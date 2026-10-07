from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from authentication.models import UserModel
from social.models import CoupleModel, SingleConnectionModel
from utils.choices import *
import uuid


class MediaModel(models.Model):
    file = models.FileField(upload_to="chats/media/")
    owner = models.ForeignKey(
        UserModel,
        on_delete=models.SET_NULL,
        related_name="legacy_chat_uploads",
        null=True,
        blank=True,
    )
    chat_room = models.ForeignKey(
        "ChatRoom",
        on_delete=models.SET_NULL,
        related_name="legacy_media",
        null=True,
        blank=True,
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Media {self.pk}"


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

    chat_type = models.CharField(
        max_length=10,
        choices=ChatTypeChoices,
        default="single",
    )

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms",
        null=True,
        blank=True,
    )
    singles = models.ForeignKey(
        SingleConnectionModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms",
        null=True,
        blank=True,
    )

    last_message = models.ForeignKey(
        "MessageModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last_message_for_rooms",
    )

    unread_count = models.JSONField(default=dict, blank=True)
    is_typing = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    # One sequence covers message and non-message changes.  Writers allocate it
    # while holding a row lock; Redis/Channels are notification hints only.
    last_sequence = models.PositiveBigIntegerField(default=0)
    # Set by the first committed v2 ciphertext message. Once set, legacy
    # plaintext writers stay disabled for this relationship room.
    encrypted_v2_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    pinned_messages = models.ManyToManyField(
        "MessageModel",
        blank=True,
        related_name="pinned_in_rooms",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Default ordering for queries:"-timestamp" ensures newest messages appear first (chat UX requirement)
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
        # Indexes to optimize chat room listing and filtering:
        # - updated_at: used for sorting chats by recent activity
        # - chat_type: quick filtering between single/couple chats
        indexes = [
            models.Index(fields=["updated_at"]),
            models.Index(fields=["chat_type"]),
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


class MessageModel(models.Model):
    chat_room = models.ForeignKey(
        ChatRoom,
        on_delete=models.CASCADE,
        related_name="messages",
        db_index=True,
    )

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="messages",
        null=True,
        blank=True,
    )
    singles = models.ForeignKey(
        SingleConnectionModel,
        on_delete=models.CASCADE,
        related_name="messages",
        null=True,
        blank=True,
    )

    sender = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    receiver = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="received_messages",
        null=True,
        blank=True,
    )

    message_type = models.CharField(
        max_length=20,
        choices=MessageType,
        default=MessageType.TEXT,
    )
    text = models.TextField(null=True, blank=True)
    profile_action = models.JSONField(null=True, blank=True, editable=False)
    profile_request_id = models.UUIDField(null=True, blank=True, editable=False)

    # V2 content is an opaque, client-created envelope.  The server deliberately
    # does not define cryptographic fields until a vetted protocol adapter is
    # selected.  Existing plaintext remains preserved and explicitly labelled.
    client_operation_id = models.UUIDField(null=True, blank=True, editable=False)
    payload_hash = models.CharField(max_length=64, blank=True, editable=False)
    sequence = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    revision = models.PositiveBigIntegerField(default=1)
    content_kind = models.CharField(max_length=24, default="legacy")
    encrypted_envelope = models.JSONField(null=True, blank=True, editable=False)
    legacy_plaintext = models.BooleanField(default=True, editable=False)
    edited_at = models.DateTimeField(null=True, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    sender_device_session = models.ForeignKey(
        "authentication.DeviceSession",
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        null=True,
        blank=True,
        editable=False,
    )

    media = models.FileField(
        upload_to="messages/media/",
        null=True,
        blank=True,
    )
    media_url = models.URLField(max_length=500, null=True, blank=True)
    sticker_url = models.URLField(max_length=500, null=True, blank=True)
    medias = models.ManyToManyField(
        MediaModel,
        related_name="messages",
        blank=True,
    )

    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )

    is_deleted = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False, db_index=True)
    is_delivered = models.BooleanField(default=False, db_index=True)
    is_sent = models.BooleanField(default=False)
    is_edited = models.BooleanField(default=False)

    deleted_message = models.CharField(max_length=100, null=True, blank=True)
    delete_option = models.CharField(
        max_length=20,
        choices=DeleteOption.choices,
        default=DeleteOption.NOT_DELETED,
    )

    # Keep temporarily for backward compatibility
    message_deletion_dict = models.JSONField(null=True, blank=True,default=dict)
    delete_for = models.JSONField(null=True, blank=True,default=dict)
    reactions = models.JSONField(null=True, blank=True,default=dict)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sender", "profile_request_id"], name="unique_profile_message_request"),
            models.UniqueConstraint(
                fields=["chat_room", "sender", "client_operation_id"],
                condition=models.Q(client_operation_id__isnull=False),
                name="unique_chat_message_operation",
            ),
            models.UniqueConstraint(
                fields=["chat_room", "sequence"],
                condition=models.Q(sequence__isnull=False),
                name="unique_chat_message_sequence",
            ),
        ]
        ordering = ["-timestamp"]
        # Database indexes to optimize high-frequency chat queries:
        # - (chat_room, -timestamp): fast message pagination per room
        # - (sender, -timestamp): fast lookup of messages sent by user
        # - (reply_to): efficient reply-thread resolution
        indexes = [
            models.Index(fields=["chat_room", "-timestamp"]),
            models.Index(fields=["sender", "-timestamp"]),
            models.Index(fields=["reply_to"]),
            models.Index(fields=["chat_room", "-sequence"], name="chat_message_sequence_idx"),
        ]

    def __str__(self):
        return f"Message {self.pk} in room {self.chat_room_id}"


class MessageDeletion(models.Model):
    message = models.ForeignKey(
        MessageModel,
        on_delete=models.CASCADE,
        related_name="deletions",
    )
    user = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="message_deletions",
        default=None,
    )
    delete_option = models.CharField(
        max_length=20,
        choices=DeleteOption.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user", "delete_option"],
                name="unique_message_user_delete_option",
            )
        ]
        indexes = [
            models.Index(fields=["message", "user"]),
        ]

    def __str__(self):
        return f"Deletion for message {self.message_id} by user {self.user_id}"


class MessageReaction(models.Model):
    message = models.ForeignKey(
        MessageModel,
        on_delete=models.CASCADE,
        related_name="reaction_records",
    )
    user = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="message_reactions",
    )
    reaction = models.CharField(
        max_length=20,
        choices=MessageReaction,
        blank=True,
        default="",
    )
    encrypted_envelope = models.JSONField(null=True, blank=True, editable=False)
    revision = models.PositiveBigIntegerField(default=1)
    is_removed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="unique_message_user_reaction",
            )
        ]
        indexes = [
            models.Index(fields=["message"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"Reaction on message {self.message_id} by user {self.user_id}"


class ChatOperation(models.Model):
    """Idempotency record committed in the same transaction as a V2 mutation."""

    chat_room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="operations")
    actor = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="chat_operations")
    operation_id = models.UUIDField()
    kind = models.CharField(max_length=32)
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chat_room", "actor", "operation_id"],
                name="unique_chat_operation",
            )
        ]
        indexes = [models.Index(fields=["chat_room", "actor", "-created_at"], name="chat_operation_actor_idx")]


class ChatChange(models.Model):
    """Durable room change stream; payload contains metadata, never plaintext."""

    chat_room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="changes")
    sequence = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=32)
    message = models.ForeignKey(
        MessageModel,
        on_delete=models.SET_NULL,
        related_name="changes",
        null=True,
        blank=True,
    )
    message_revision = models.PositiveBigIntegerField(null=True, blank=True)
    actor = models.ForeignKey(
        UserModel,
        on_delete=models.SET_NULL,
        related_name="chat_changes",
        null=True,
    )
    audience_user = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="private_chat_changes",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["chat_room", "sequence"], name="unique_chat_change_sequence")
        ]
        indexes = [models.Index(fields=["chat_room", "sequence"], name="chat_change_sequence_idx")]


class ChatOutbox(models.Model):
    """A committed change waiting to be published as a best-effort live hint."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        PUBLISHING = "publishing", "Publishing"
        PUBLISHED = "published", "Published"

    change = models.OneToOneField(ChatChange, on_delete=models.CASCADE, related_name="outbox")
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["state", "available_at", "id"], name="chat_outbox_pending_idx")]


class ChatReceipt(models.Model):
    chat_room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="receipt_cursors")
    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="chat_receipt_cursors")
    delivered_through = models.PositiveBigIntegerField(default=0)
    read_through = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["chat_room", "user"], name="unique_chat_receipt"),
            models.CheckConstraint(
                condition=models.Q(read_through__lte=models.F("delivered_through")),
                name="chat_receipt_read_lte_delivered",
            ),
        ]


class MessageVisibility(models.Model):
    message = models.ForeignKey(MessageModel, on_delete=models.CASCADE, related_name="visibility_records")
    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="chat_message_visibility")
    hidden_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="unique_chat_message_visibility")
        ]


class EncryptedAttachment(models.Model):
    """Room-scoped staged ciphertext.  The server never receives plaintext media."""

    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        CLAIMED = "claimed", "Claimed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat_room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="encrypted_attachments")
    owner = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="encrypted_chat_attachments")
    operation_id = models.UUIDField()
    ciphertext = models.FileField(upload_to="chat_encrypted/")
    ciphertext_sha256 = models.CharField(max_length=64)
    ciphertext_size = models.PositiveBigIntegerField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.STAGED)
    claimed_message = models.ForeignKey(
        MessageModel,
        on_delete=models.SET_NULL,
        related_name="encrypted_attachments",
        null=True,
        blank=True,
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chat_room", "owner", "operation_id"],
                name="unique_encrypted_attachment_operation",
            )
        ]
        indexes = [models.Index(fields=["state", "expires_at"], name="chat_attachment_expiry_idx")]
