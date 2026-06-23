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
    couple_connection = models.OneToOneField(
        CoupleConnectionModel,
        on_delete=models.CASCADE,
        related_name="couple",
        null=True,
        blank=True
    )

    male_partner = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="couples_as_male"
    )

    female_partner = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="couples_as_female"
    )

    anniversary_date = models.DateField(null=True, blank=True)

    shared_dreams = models.JSONField(default=list, blank=True)
    shared_interests = models.JSONField(default=list, blank=True)

    relationship_tagline = models.CharField(max_length=80, null=True, blank=True)

    nickname_for_male = models.CharField(max_length=30, null=True, blank=True)
    nickname_for_female = models.CharField(max_length=30, null=True, blank=True)

    couple_profile_photo = models.ImageField(
        upload_to="couples/profile_photos/",
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Couple"
        verbose_name_plural = "Couples"

    def __str__(self):
        return f"{self.male_partner} ❤️ {self.female_partner}"

class CoupleMomentModel(models.Model):
    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="moments"
    )

    created_by = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="created_couple_moments"
    )

    title = models.CharField(max_length=100, null=True, blank=True)

    caption = models.TextField(max_length=1000)

    moment_date = models.DateField()

    mood = models.CharField(
        max_length=30,
        choices=MomentMood.choices,
        null=True,
        blank=True
    )

    location_name = models.CharField(max_length=120, null=True, blank=True)

    visibility = models.CharField(
        max_length=20,
        choices=MomentVisibility.choices,
        default=MomentVisibility.PRIVATE
    )

    tags = models.JSONField(default=list, blank=True)

    partner_memory = models.TextField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="Partner's perspective on this moment."
    )

    is_time_capsule = models.BooleanField(default=False)

    unlock_date = models.DateField(
        null=True,
        blank=True,
        help_text="If time capsule, moment unlocks on this date."
    )

    is_archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-moment_date", "-created_at"]
        verbose_name = "Couple Moment"
        verbose_name_plural = "Couple Moments"

    def __str__(self):
        return f"{self.couple} - {self.title or self.moment_date}"

class CoupleMomentPhotoModel(models.Model):
    moment = models.ForeignKey(
        CoupleMomentModel,
        on_delete=models.CASCADE,
        related_name="photos"
    )

    image = models.ImageField(upload_to="couples/moments/")

    order = models.PositiveSmallIntegerField(default=0)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "uploaded_at"]

    def __str__(self):
        return f"Photo for {self.moment}"

# @deprecated("Use the new CoupleMomentPhotoModel instead.")
class PhotoAlbumModel(models.Model): #This model is now deprecated and will be removed in future releases. Please use CoupleMomentPhotoModel instead.
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
