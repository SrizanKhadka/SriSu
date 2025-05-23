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
        full_name = self.fake.first_name() + " " + self.fake.last_name()
        
        # male_url = self.generate_unsplash_url(query="man portrait")
        female_url = self.generate_unsplash_url(query="woman portrait")

        user = UserModel.objects.create(
            phone_number=phone_number,
            profile_photo=female_url,
            full_name=full_name,
            gender=GenderChoices.FEMALE,
            zodiac_sign=ZodiacSignChoices.ARIES,
            mood=MoodChoices.HAPPY,
            city= "Kathmandu",
            country="Nepal",
            bio=self.fake.text(max_nb_chars=200),
            username=self.fake.user_name(),
            dob=self.fake.date_of_birth(minimum_age=18, maximum_age=40),
            is_profile_complete=True,
            is_phone_verified=True,
        )
        return user

    def handle(self, *args, **options):
        for _ in range(10):
            user = self.create_fake_user()
            self.stdout.write(self.style.SUCCESS(f"✅ Created user: {user.full_name} ({user.phone_number})"))
    
    def generate_unsplash_url(self,query="person", width=300, height=300):
        return f"https://source.unsplash.com/{width}x{height}/?{query}"
            