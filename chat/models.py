from django.core.exceptions import ValidationError
from django.db import models
from authentication.models import UserModel
from social.models import CoupleModel, SingleConnectionModel
from utils.choices import *
import uuid


class MediaModel(models.Model):
    file = models.FileField(upload_to="chats/media/")
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
        constraints = [ # Prevent duplicate chat rooms between the same pair of users. Combined with user swapping in save(), ensures (A,B) and (B,A) are treated the same.
            models.UniqueConstraint(
                fields=["user_one", "user_two"],
                name="unique_chatroom_users",
            )
        ]
        # Indexes to optimize chat room listing and filtering:
        # - updated_at: used for sorting chats by recent activity
        # - chat_type: quick filtering between single/couple chats
        indexes = [
            models.Index(fields=["updated_at"]),
            models.Index(fields=["chat_type"]),
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
    message_deletion_dict = models.JSONField(null=True, blank=True)
    delete_for = models.JSONField(null=True, blank=True)
    reactions = models.JSONField(null=True, blank=True)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        # Database indexes to optimize high-frequency chat queries:
        # - (chat_room, -timestamp): fast message pagination per room
        # - (sender, -timestamp): fast lookup of messages sent by user
        # - (reply_to): efficient reply-thread resolution
        indexes = [
            models.Index(fields=["chat_room", "-timestamp"]),
            models.Index(fields=["sender", "-timestamp"]),
            models.Index(fields=["reply_to"]),
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
    )
    created_at = models.DateTimeField(auto_now_add=True)

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