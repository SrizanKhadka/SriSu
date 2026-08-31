from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
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

    members = models.ManyToManyField(
        UserModel,
        through="CoupleMembershipModel",
        related_name="couple_profiles",
    )

    anniversary_date = models.DateField(null=True, blank=True)

    shared_dreams = models.JSONField(default=list, blank=True)
    shared_interests = models.JSONField(default=list, blank=True)

    title = models.CharField(max_length=100, null=True, blank=True)
    relationship_tagline = models.CharField(max_length=80, null=True, blank=True)
    journey_story = models.TextField(max_length=3000, null=True, blank=True)
    relationship_strength = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    cover_photo = models.ImageField(
        upload_to="couples/profile_photos/",
        null=True,
        blank=True
    )

    profile_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Couple"
        verbose_name_plural = "Couples"

    def __str__(self):
        names = list(self.members.values_list("full_name", flat=True)[:2])
        return " ❤️ ".join(name for name in names if name) or f"Couple {self.pk}"


class CoupleMembershipModel(models.Model):
    class Position(models.IntegerChoices):
        PARTNER_ONE = 1, "Partner one"
        PARTNER_TWO = 2, "Partner two"

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.OneToOneField(
        UserModel,
        on_delete=models.CASCADE,
        related_name="couple_membership",
    )
    position = models.PositiveSmallIntegerField(choices=Position.choices)
    nickname = models.CharField(max_length=30, null=True, blank=True)
    is_owner = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["couple", "position"],
                name="unique_couple_member_position",
            ),
            models.UniqueConstraint(
                fields=["couple", "user"],
                name="unique_user_per_couple",
            ),
        ]
        indexes = [models.Index(fields=["couple", "position"])]

    def __str__(self):
        return f"{self.user} in {self.couple_id}"

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
        return f"Photo for {self.couple}"

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
