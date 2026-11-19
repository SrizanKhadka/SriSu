import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.timezone import now
from utils.choices import GenderChoices, MoodChoices, OtpStatusChoices, ZodiacSignChoices

class CustomUserManager(BaseUserManager):
    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("The Phone Number field must be set.")

        user = self.model(phone_number=phone_number, **extra_fields)

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone_number=phone_number, password=password, **extra_fields)


class UserModel(AbstractUser):
    phone_number = models.CharField(max_length=15, unique=True)
    profile_photo = models.ImageField(upload_to="profiles/", null=True, blank=True)

    # Keep Django auth fields inherited from AbstractUser.
    # Only override where needed safely.
    username = models.CharField(max_length=150, null=True, blank=True, unique=False)
    email = models.EmailField(null=True, blank=True)
    password = models.CharField(max_length=128, null=True, blank=True)

    bio = models.TextField(null=True, blank=True)
    full_name = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)

    gender = models.CharField(max_length=10, choices=GenderChoices, null=True, blank=True)
    zodiac_sign = models.CharField(max_length=20, choices=ZodiacSignChoices, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    mood = models.CharField(max_length=50, choices=MoodChoices, null=True, blank=True)
    
    is_engaged = models.BooleanField(default=False,null=True, blank=True)

    profile_photo_skipped = models.BooleanField(default=False, db_default=False)
    is_profile_complete = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)

    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["username"], condition=models.Q(username__isnull=False) & ~models.Q(username=""), name="auth_unique_nonempty_username"),
        ]
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_date"]
        indexes = [
            models.Index(fields=["phone_number", "full_name", "gender", "zodiac_sign", "city", "country"]),
        ]

    def __str__(self):
        return self.full_name or self.phone_number


class UserPhotoAlbumModel(models.Model):
    user = models.ForeignKey("UserModel", on_delete=models.CASCADE, related_name="user_photos")
    photo = models.ImageField(upload_to="user_album/", null=True, blank=True)
    removed = models.BooleanField(default=False)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.user.full_name or self.user.phone_number


class InterestCategory(models.Model):
    name = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)

    def __str__(self):
        return self.label


class InterestModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(
        "InterestCategory",
        on_delete=models.CASCADE,
        related_name="interests",
        null=True,
        blank=True,
    )
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Interest"
        verbose_name_plural = "Interests"
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserInterestModel(models.Model):
    user = models.ForeignKey("UserModel", on_delete=models.CASCADE, related_name="user_interests")
    name = models.CharField(max_length=100)
    interest = models.ForeignKey(
        "InterestModel",
        on_delete=models.CASCADE,
        related_name="user_interests",
        null=True,
        blank=True,
    )
    removed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "User Interest"
        verbose_name_plural = "User Interests"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["user", "name"]),
        ]

    def __str__(self):
        return f"{self.user.full_name or self.user.phone_number} - {self.name}"


class OtpModel(models.Model):
    phone_number = models.CharField(max_length=15, unique=True)
    # HMAC only; old plaintext challenges are invalidated by the migration.
    otp_code = models.CharField(max_length=128)
    challenge_id = models.UUIDField(default=uuid.uuid4)
    request_id = models.UUIDField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    resend_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    delivery_state = models.CharField(max_length=12, default="failed")
    otp_status = models.CharField(
        max_length=15,
        choices=OtpStatusChoices,
        default=OtpStatusChoices.NOTHING,
    )
    last_request_time = models.DateTimeField(default=now)
    otp_attempts = models.IntegerField(default=0)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.phone_number

class OtpRequestBudget(models.Model):
    """Database-backed spend guard shared across workers; contains no phone/IP.

    Keys are keyed digests. Fixed rows reset their windows rather than grow per request.
    """
    key = models.CharField(max_length=80, unique=True)
    window_start = models.DateTimeField(default=now)
    count = models.PositiveIntegerField(default=0)


class DeviceSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="device_sessions")
    refresh_digest = models.CharField(max_length=64)
    previous_refresh_digest = models.CharField(max_length=64, blank=True)
    rotation_id = models.UUIDField(default=uuid.uuid4)
    issued_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
