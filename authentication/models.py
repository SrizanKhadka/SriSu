from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from utils.choices import *
from django.utils.timezone import now


class CustomUserManager(BaseUserManager):
    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("The Phone Number field must be set.")
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
 
    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone_number, password, **extra_fields)


class UserModel(AbstractUser):
    phone_number = models.CharField(max_length=15, unique=True)
    profile_photo = models.ImageField(upload_to="profiles/", null=True, blank=True)
    username = models.CharField(null=True,blank=True)
    email = models.CharField(null=True,blank=True)
    password = models.CharField(null=True,blank=True)
    username = models.CharField(null=True,blank=True)
    email = models.CharField(null=True,blank=True)
    bio = models.TextField(null=True, blank=True)
    password = models.CharField(null=True,blank=True)
    full_name = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100,null=True,blank=True)
    country = models.CharField(max_length=100,null=True,blank=True)
    gender = models.CharField(
        max_length=10, choices=GenderChoices, null=True, blank=True
    )
    zodiac_sign = models.CharField(
        max_length=20, choices=ZodiacSignChoices, null=True, blank=True
    )
    dob = models.DateField(null=True, blank=True)
    mood = models.CharField(max_length=50, choices=MoodChoices, null=True, blank=True)
    is_profile_complete = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)
    
    REQUIRED_FIELDS = []
    USERNAME_FIELD = 'phone_number'
    
    objects = CustomUserManager()
    
    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_date"]
        indexes = [
            models.Index(fields=["phone_number","full_name","gender","zodiac_sign","city","country"]),
        ]

    def __str__(self):
        return self.full_name or self.phone_number


class UserPhotoAlbumModel(models.Model):
    user = models.ForeignKey("UserModel", on_delete=models.CASCADE, related_name="user_photos")
    photo = models.ImageField(upload_to="user_album/", null=True, blank=True)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.full_name or self.user.phone_number

class UserInterestModel(models.Model):
    user = models.ForeignKey("UserModel", on_delete=models.CASCADE, related_name="user_interests")
    name = models.CharField(max_length=100)
    
    class Meta:
        verbose_name = "User Interest"
        verbose_name_plural = "User Interests"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["user", "name"]),
        ]
    
    def __str__(self):
        return f"{self.user.full_name} - {self.name}"
    
class OtpModel(models.Model):
    phone_number = models.CharField(max_length=15,unique=True)
    otp_code = models.CharField(max_length=6)
    otp_status = models.CharField(
        max_length=15, choices=OtpStatusChoices, default=OtpStatusChoices.NOTHING
    )
    last_request_time = models.DateTimeField(default=now)
    otp_attempts = models.IntegerField(default=0)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.phone_number
