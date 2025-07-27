from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from authentication.models import UserPhotoAlbumModel, UserModel

class Command(BaseCommand):
    help = "Seed dummy users into the UserModel table"
    
    photos = [
        "https://shorturl.at/SbTkf",
        "https://shorturl.at/48rLa",
        "https://shorturl.at/jAZgX",
    ]

    def __init__(self):
        super().__init__()
        self.fake = Faker()

    def create_fake_user_Photos(self):
        
        users = UserModel.objects.all()
        
        for user in users:
            for photo in self.photos:
                UserPhotoAlbumModel.objects.create(
                    user=user,
                    photo=photo
                )
                self.stdout.write(self.style.SUCCESS(f"✅ Created interest '{photo}' for user: {user.full_name} ({user.phone_number})"))


    def handle(self, *args, **options):
        self.create_fake_user_Photos()

            