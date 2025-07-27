from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from chat.models import CoupleConnectionModel, SingleConnectionModel
from authentication.models import UserModel

class Command(BaseCommand):
    help = "Seed dummy users into the UserModel table"

    def __init__(self):
        super().__init__()
        self.fake = Faker()
    
    def create_dummy_connections(self):
        
        users = UserModel.objects.all()
        
        for user in users:
            # Generate a random number for the sender and receiver
            sender_number = "+18938278981"
            receiver_number = user.phone_number
            
            if sender_number == receiver_number:
                # Skip if the sender and receiver are the same
                continue
            # Create a dummy couple connection
            CoupleConnectionModel.objects.create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                connection_status=CoupleConnectionStatus.PENDING,
                created_at=self.fake.date_time_this_year(),
                updated_at=self.fake.date_time_this_year(),
            )
            self.stdout.write(self.style.SUCCESS("Dummy connections created successfully."))
    
    def create_dummy_single_connections(self):
        users = UserModel.objects.all()
        
        for user in users:
            # Generate a random number for the sender and receiver
            sender_number = "+18938278981"
            receiver_number = user.phone_number
            
            if sender_number == receiver_number:
                # Skip if the sender and receiver are the same
                continue
            # Create a dummy couple connection
            SingleConnectionModel.objects.create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                connection_status=CoupleConnectionStatus.PENDING,
                created_at=self.fake.date_time_this_year(),
                updated_at=self.fake.date_time_this_year(),
            )
            self.stdout.write(self.style.SUCCESS("Dummy Single connections created successfully."))
    
    def handle(self, *args, **options):
        self.create_dummy_single_connections()
