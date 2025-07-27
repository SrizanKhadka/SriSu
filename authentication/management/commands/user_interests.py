from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from authentication.models import UserInterestModel, UserModel

class Command(BaseCommand):
    help = "Seed dummy users into the UserModel table"
    
    interests = [
        "Traveling",
        "Cooking",
        "Reading",
        "Gaming",
        "Music",
        "Sports",
        "Photography",
        "Art",
        "Technology",
        "Fitness"
    ]

    def __init__(self):
        super().__init__()
        self.fake = Faker()

    def create_fake_user_Interests(self):
        
        users = UserModel.objects.all()
        
        for user in users:
            for interest in self.interests:
                UserInterestModel.objects.create(
                    user=user,
                    name=interest
                )
                self.stdout.write(self.style.SUCCESS(f"✅ Created interest '{interest}' for user: {user.full_name} ({user.phone_number})"))


    def handle(self, *args, **options):
        self.create_fake_user_Interests()

            