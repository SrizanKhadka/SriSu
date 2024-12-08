from django.db import models
from django.contrib.auth.models import AbstractUser, AbstractBaseUser
from utils.choices import *


class UserModel(AbstractUser):
    phone_number = models.CharField(max_length=15, unique=True)
    profile_photo = models.ImageField(upload_to="profiles/", null=True, blank=True)
    username = models.CharField(null=True,blank=True)
    password = models.CharField(null=True,blank=True)
    full_name = models.CharField(max_length=100, null=True, blank=True)
    gender = models.CharField(
        max_length=10, choices=GenderChoices, null=True, blank=True
    )
    zodiac_sign = models.CharField(
        max_length=20, choices=ZodiacSignChoices, null=True, blank=True
    )
    dob = models.DateField(null=True, blank=True)
    mood = models.CharField(max_length=50, choices=MoodChoices, null=True, blank=True)
    is_profile_complete = models.BooleanField(default=False)  # For redirection logic
    is_phone_verified = models.BooleanField(default=False)
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name or self.phone_number


class OtpModel(models.Model):
    phone_number = models.CharField(max_length=15,unique=True)
    otp_code = models.CharField(max_length=6)
    otp_status = models.CharField(
        max_length=15, choices=OtpStatusChoices, default=OtpStatusChoices.NOTHING
    )
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.phone_number
