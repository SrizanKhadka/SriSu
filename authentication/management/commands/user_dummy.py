from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from authentication.models import UserModel

class Command(BaseCommand):
    help = "Seed dummy users into the UserModel table"

    def __init__(self):
        super().__init__()
        self.fake = Faker()

    def create_fake_user(self):
        phone_number = "+977" + self.fake.msisdn()[:8]
        full_name = self.fake.name()
        last_name = self.fake.last_name()

        user = UserModel.objects.create(
            phone_number=phone_number,
            profile_photo=self.fake.image_url(),
            full_name=full_name,
            gender=GenderChoices.MALE,
            zodiac_sign=ZodiacSignChoices.ARIES,
            mood=MoodChoices.HAPPY,
            last_name=last_name,
            dob=self.fake.date_of_birth(minimum_age=18, maximum_age=40),
            is_profile_complete=True,
            is_phone_verified=True,
        )
        return user

    def handle(self, *args, **options):
        for _ in range(10):
            user = self.create_fake_user()
            self.stdout.write(self.style.SUCCESS(f"✅ Created user: {user.full_name} ({user.phone_number})"))
            