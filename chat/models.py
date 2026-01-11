from django.db import models
from authentication.models import UserModel
from utils.choices import *
from social.models import (
    CoupleModel,
    SingleConnectionModel,
    CoupleConnectionModel,
    PhotoAlbumModel,
)
import uuid


class MediaModel(models.Model):
    file = models.FileField(upload_to="chats_media/")
    uploaded_at = models.DateTimeField(auto_now_add=True)


class MessageModel(models.Model):

    # chat associates
    chat_room = models.ForeignKey(
        "chat.ChatRoom",
        on_delete=models.CASCADE,
        related_name="message_models",
        null=True,
        blank=True,
    )
    couple = models.ForeignKey(
        CoupleModel, on_delete=models.CASCADE, related_name="message_models"
    )
    singles = models.ForeignKey(
        SingleConnectionModel,
        on_delete=models.CASCADE,
        related_name="message_models",
        null=True,
        blank=True,
    )

    # sender and receiver
    sender = models.ForeignKey(
        UserModel, on_delete=models.CASCADE, related_name="sent_messages"
    )
    receiver = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="received_messages",
        null=True,
        blank=True,
    )

    # Message contents
    message_type = models.CharField(
        max_length=10, choices=MessageType, default=MessageType.TEXT
    )
    text = models.TextField(null=True, blank=True)
    media = models.FileField(
        upload_to="messages/media/",
        null=True,
        blank=True,
    )
    media_url = models.URLField(max_length=500, null=True, blank=True)
    sticker_url = models.URLField(blank=True, null=True)
    medias = models.ManyToManyField(
        MediaModel, related_name="message_models", blank=True
    )  # Many-to-Many Relationship with MediaModel (for multiple media per message)

    # Reply to message
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )

    # Message status
    is_deleted = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)

    # Message actions
    deleted_message = models.CharField(max_length=100, null=True, blank=True)
    delete_option = models.CharField(
        max_length=20, choices=DeleteOption.choices, default=DeleteOption.NOT_DELETED
    )
    message_deletion_dict = models.JSONField(
        null=True, blank=True
    )  # Example: {"user_id_1": "delete_for_me", "user_id_2": "delete_for_everyone"}
    is_edited = models.BooleanField(default=False)

    delete_for = models.JSONField(
        null=True, blank=True
    )  # Example: { "user": [ {"user_id": 1, "delete_option": "DELETE_FOR_ME"}, ... ] }

    # reactions
    reactions = models.JSONField(
        null=True, blank=True
    )

    # Timestamps
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.couple.male_partner.full_name} - {self.couple.female_partner.full_name}"


class MessageDeletion(models.Model):
    messageModel = models.ForeignKey(
        MessageModel, on_delete=models.CASCADE, related_name="deletions"
    )

    user_id = models.IntegerField()
    delete_option = models.CharField(
        max_length=20, choices=DeleteOption.choices, null=True, blank=True
    )

    class Meta:
        unique_together = ("messageModel", "user_id", "delete_option")
        indexes = [
            models.Index(fields=["messageModel", "user_id", "delete_option"]),
        ]


class MessageReaction(models.Model):
    messageModel = models.ForeignKey(
        MessageModel, on_delete=models.CASCADE, related_name="messages"
    )

    reaction = models.CharField(
        max_length=20, choices=MessageReaction, null=True, blank=True
    )


class ChatRoom(models.Model):

    id = models.UUIDField(
        primary_key=True, unique=True, default=uuid.uuid4, editable=False
    )
    chat_type = models.CharField(
        max_length=10, choices=ChatTypeChoices, default="single"
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

    # Messages
    messages = models.ManyToManyField(
        MessageModel, related_name="chat_rooms", blank=True
    )

    # Chat metadata
    last_message = models.ForeignKey(
        MessageModel,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last_message_chat",
        default="",
    )
    unread_count = models.JSONField(
        default=dict, blank=True, null=True
    )  # Example: {"user_1": 5, "user_2": 3}

    # Extra features
    is_typing = models.JSONField(
        default=dict, blank=True, null=True
    )  # Example: {"user_1": True, "user_2": False}
    pinned_messages = models.ManyToManyField(
        MessageModel, blank=True, related_name="pinned_in_chat"
    )
    settings = models.JSONField(
        default=dict, blank=True, null=True
    )  # Example: {"muted": True, "archived": False}

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ChatRoom"
        verbose_name_plural = "ChatRooms"
