from django.db import models
from utils.choices import CoupleConnectionStatus
from authentication.models import UserModel
from utils.choices import MessageType, MessageReaction


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
        unique_together = [
            "sender_number",
            "receiver_number",
        ]  # Prevent duplicate requests

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


class MessageModel(models.Model):
    couple = models.ForeignKey(
        CoupleModel, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        UserModel, on_delete=models.CASCADE, related_name="sent_messages"
    )
    message_type = models.CharField(
        max_length=10, choices=MessageType, default=MessageType.TEXT
    )
    text = models.TextField(null=True, blank=True)
    media = models.FileField(
        upload_to="messages/media/",
        null=True,
        blank=True,
    )
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )
    is_deleted = models.BooleanField(default=False)
    deleted_message = models.CharField(max_length=100, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.couple.male_partner.full_name} - {self.couple.female_partner.full_name}"

    def delete_for_everyone(self):
        """Marks the message as deleted for all users."""
        self.is_deleted = True
        self.save()

    def delete_for_self(self, user):
        """Implements user-specific deletion (if necessary in the app logic)."""
        pass  # Custom logic for handling "delete for self" if required.


class MessageReaction(models.Model):
    messageModel = models.ForeignKey(
        MessageModel, on_delete=models.CASCADE, related_name="messages"
    )

    reaction = models.CharField(
        max_length=20, choices=MessageReaction, null=True, blank=True
    )
