from django.db import models
from authentication.models import UserModel
from utils.choices import *

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
        indexes = [
            models.Index(fields=["sender_number", "receiver_number","connection_status"]),
        ]

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
    shared_dreams = models.JSONField(null=True, blank=True)
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

class SingleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=SingleConnectionStatus,
        default=SingleConnectionStatus.NOTHING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        # unique_together = [
        #     "sender_number",
        #     "receiver_number",
        # ]  # Prevent duplicate requests

        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["sender_number", "receiver_number","connection_status"]),
        ]

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"

class UserPreferenceModel(models.Model):
    user = models.OneToOneField(UserModel, on_delete=models.CASCADE, related_name="user_preferences")
    min_age = models.IntegerField(null=True, blank=True,default=18)
    max_age = models.IntegerField(null=True, blank=True,default=35)
    zodiac_sign = models.CharField(
        max_length=20, choices=ZodiacSignChoices, null=True, blank=True
    )
    radius_km = models.IntegerField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)    
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "User Preference"
        verbose_name_plural = "User Preferences"
        ordering = ["user"]
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return self.user.full_name or self.user.phone_number
