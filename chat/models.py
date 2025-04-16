from django.db import models
from utils.choices import CoupleConnectionStatus, SingleConnectionStaus
from authentication.models import UserModel
from utils.choices import *
import uuid

class CoupleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=CoupleConnectionStatus,
        default=CoupleConnectionStatus.NOTHING,
    )
    breakup_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # unique_together = [
        #     "sender_number",
        #     "receiver_number",
        # ]  # Prevent duplicate requests

        ordering = ["-updated_at"]
        verbose_name = "Couple_Connection"

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"


class CoupleModel(models.Model):
    couple_connection_model = models.ForeignKey(
        CoupleConnectionModel,
        on_delete=models.CASCADE,
        related_name="couple_connection_model",
    )

    male_partner = models.ForeignKey(
        UserModel, on_delete=models.CASCADE, related_name="male_partner"
    )

    female_partner = models.ForeignKey(
        UserModel, on_delete=models.CASCADE, related_name="female_partner"
    )
    anniversary_date = models.DateField(null=True, blank=True)
    shared_dreams = (models.JSONField(null=True, blank=True),)
    shared_interests = models.JSONField(null=True, blank=True)
    relationship_tagline = models.CharField(max_length=30, null=True, blank=True)
    photo_album = models.JSONField(null=True, blank=True)
    nickname_for_male = models.CharField(max_length=30, null=True, blank=True)
    nickname_for_female = models.CharField(max_length=30, null=True, blank=True)
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Timestamp when the couple was created."
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Timestamp when the couple data was last updated."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Couple"
        verbose_name_plural = "Couples"

    # def clean(self):
    #     if self.photo_album.count() > 10:
    #         raise ValidationError("You can only upload up to 10 photos.")

    def __str__(self):
        return f"{self.male_partner} ❤️ {self.female_partner}"


class PhotoAlbumModel(models.Model):
    couple = models.ForeignKey(
        CoupleModel, on_delete=models.CASCADE, related_name="couple_photo_album"
    )
    photo = models.ImageField(upload_to="couple_album/", null=True, blank=True)

    def __str__(self):
        return f"{self.couple.male_partner} ❤️ {self.couple.female_partner}"

class MediaModel(models.Model):
    file = models.FileField(upload_to="chats_media/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

class SingleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=SingleConnectionStaus,
        default=SingleConnectionStaus.NOTHING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        # unique_together = [
        #     "sender_number",
        #     "receiver_number",
        # ]  # Prevent duplicate requests

        ordering = ["-updated_at"]
        verbose_name = "Couple_Connection"

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"


class MessageModel(models.Model):
    
    #chat associates
    chat_room = models.ForeignKey("chat.ChatRoom", on_delete=models.CASCADE, related_name="message_models",null=True, blank=True)
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="message_models")
    singles = models.ForeignKey(SingleConnectionModel, on_delete=models.CASCADE, related_name="message_models",null=True, blank=True)
    
    #sender and receiver
    sender = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="sent_messages")
    receiver = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="received_messages",null=True,blank=True)
    
    #Message contents
    message_type = models.CharField(max_length=10, choices=MessageType, default=MessageType.TEXT)
    text = models.TextField(null=True, blank=True)
    media = models.FileField(
        upload_to="messages/media/",
        null=True,
        blank=True,
    )
    media_url = models.URLField(max_length=500,null=True,blank=True)
    sticker_url = models.URLField(blank=True, null=True)
    medias = models.ManyToManyField(MediaModel, related_name="message_models", blank=True)     # Many-to-Many Relationship with MediaModel (for multiple media per message)
    
    #Reply to message
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )
    
    #Message status
    is_deleted = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)
    
    #Message actions
    deleted_message = models.CharField(max_length=100, null=True, blank=True)
    delete_option = models.CharField(
        max_length=20, 
        choices=DeleteOption.choices, 
        default=DeleteOption.NOT_DELETED
    )
    message_deletion_dict = models.JSONField(null=True, blank=True)  # Example: {"user_id_1": "delete_for_me", "user_id_2": "delete_for_everyone"}
    is_edited = models.BooleanField(default=False)
    
    delete_for = models.JSONField(default=dict)
    
    #reactions
    reactions = models.JSONField(default=dict)  # Example: { "user_id_1": "❤️", "user_id_2": "😂" }

    #Timestamps
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.couple.male_partner.full_name} - {self.couple.female_partner.full_name}"

class MessageReaction(models.Model):
    messageModel = models.ForeignKey(
        MessageModel, on_delete=models.CASCADE, related_name="messages"
    )

    reaction = models.CharField(
        max_length=20, choices=MessageReaction, null=True, blank=True
    )


class ChatRoom(models.Model):
    
    id = models.UUIDField(primary_key=True, unique=True, default=uuid.uuid4, editable=False)
    chat_type = models.CharField(max_length=10, choices=ChatTypeChoices, default="single")

    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="chat_rooms",null=True, blank=True)
    singles = models.ForeignKey(SingleConnectionModel, on_delete=models.CASCADE, related_name="chat_rooms",null=True, blank=True)
    
    # Messages
    messages = models.ManyToManyField(MessageModel, related_name="chat_rooms",null=True, blank=True)

    # Chat metadata
    last_message = models.ForeignKey(MessageModel, null=True, blank=True, on_delete=models.SET_NULL, related_name="last_message_chat",default="")
    unread_count = models.JSONField(default=dict,blank=True,null=True)  # Example: {"user_1": 5, "user_2": 3}
    
    # Extra features
    is_typing = models.JSONField(default=dict,blank=True,null=True)  # Example: {"user_1": True, "user_2": False}
    pinned_messages = models.ManyToManyField(MessageModel, blank=True, related_name="pinned_in_chat")
    settings = models.JSONField(default=dict,blank=True,null=True)  # Example: {"muted": True, "archived": False}
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ChatRoom"
        verbose_name_plural = "ChatRooms"