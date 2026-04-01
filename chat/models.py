from django.db import models
from authentication.models import UserModel
from utils.choices import *
from social.models import (
    CoupleModel,
    SingleConnectionModel,
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
        CoupleModel, on_delete=models.CASCADE, related_name="message_models", null=True, blank=True,
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
        if self.couple:
            male = getattr(self.couple.male_partner, "full_name", "Unknown")
            female = getattr(self.couple.female_partner, "full_name", "Unknown")
            return f"{male} - {female}"

        if self.singles:
            user_one = self.singles.sender_number
            user_two = self.singles.receiver_number
            return f"{user_one} - {user_two}"

        return f"Message {self.id}"



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
        primary_key=True, default=uuid.uuid4, editable=False
    )

    user_one = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms_as_user_one",
        null=True, blank=True,
    )
    user_two = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="chat_rooms_as_user_two",
        null=True, blank=True,
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

    messages = models.ManyToManyField(
        MessageModel, related_name="chat_rooms", blank=True
    )

    last_message = models.ForeignKey(
        MessageModel,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last_message_chat",
    )

    unread_count = models.JSONField(default=dict, blank=True)
    is_typing = models.JSONField(default=dict, blank=True)
    pinned_messages = models.ManyToManyField(
        MessageModel, blank=True, related_name="pinned_in_chat"
    )
    settings = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ChatRoom"
        verbose_name_plural = "ChatRooms"

        # Prevent duplicate rooms between same users
        constraints = [
            models.UniqueConstraint(
                fields=["user_one", "user_two"],
                name="unique_chatroom_users"
            )
        ]

    def save(self, *args, **kwargs):
        if self.user_one_id and self.user_two_id and self.user_one_id > self.user_two_id:
            self.user_one, self.user_two = self.user_two, self.user_one
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"ChatRoom {self.id} - {self.user_one.full_name} and {self.user_two.full_name}"

