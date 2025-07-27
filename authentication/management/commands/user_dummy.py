from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from authentication.models import UserModel
import random

class Command(BaseCommand):
    help = "Seed dummy users into the UserModel table"

    def __init__(self):
        super().__init__()
        self.fake = Faker()

        # Mapping of countries to their cities
        self.country_city_map = {
            "Nepal": ["Kathmandu", "Pokhara", "Lalitpur", "Biratnagar"],
            "India": ["Delhi", "Mumbai", "Bangalore", "Chennai"],
            "Bhutan": ["Thimphu", "Paro", "Punakha"],
            "Sri Lanka": ["Colombo", "Kandy", "Galle"],
            "Bangladesh": ["Dhaka", "Chittagong", "Khulna"],
            "United States": ["New York", "Los Angeles", "Chicago", "Houston"],
            "United Kingdom": ["London", "Manchester", "Birmingham", "Liverpool"],
            "Canada": ["Toronto", "Vancouver", "Montreal", "Calgary"],
            "Australia": ["Sydney", "Melbourne", "Brisbane", "Perth"],
            "Germany": ["Berlin", "Munich", "Frankfurt", "Hamburg"],
            "France": ["Paris", "Marseille", "Lyon", "Toulouse"],
            "Japan": ["Tokyo", "Osaka", "Kyoto", "Hiroshima"],
            "South Korea": ["Seoul", "Busan", "Incheon", "Daegu"],
            "China": ["Beijing", "Shanghai", "Guangzhou", "Shenzhen"],
            "Brazil": ["Sao Paulo", "Rio de Janeiro", "Salvador", "Belo Horizonte"],
            "Mexico": ["Mexico City", "Guadalajara", "Monterrey", "Cancun"],
            "Russia": ["Moscow", "Saint Petersburg", "Novosibirsk", "Yekaterinburg"],
            "Italy": ["Rome", "Milan", "Naples", "Turin"],
            "Spain": ["Madrid", "Barcelona", "Valencia", "Seville"],
            "Turkey": ["Istanbul", "Ankara", "Izmir", "Bursa"],
            "South Africa": ["Cape Town", "Johannesburg", "Durban", "Pretoria"],
            "Egypt": ["Cairo", "Alexandria", "Giza", "Sharm El Sheikh"],
            "Argentina": ["Buenos Aires", "Córdoba", "Rosario", "Mendoza"],
            "Chile": ["Santiago", "Valparaíso", "Concepción", "La Serena"],
            "Peru": ["Lima", "Arequipa", "Trujillo", "Chiclayo"],
            "Colombia": ["Bogotá", "Medellín", "Cali", "Barranquilla"],
            "Venezuela": ["Caracas", "Maracaibo", "Valencia", "Barquisimeto"],
            "Philippines": ["Manila", "Cebu City", "Davao City", "Quezon City"],
            "Indonesia": ["Jakarta", "Surabaya", "Bandung", "Medan"],
            "Malaysia": ["Kuala Lumpur", "George Town", "Johor Bahru", "Ipoh"],
            "Singapore": ["Singapore City"],
            "Thailand": ["Bangkok", "Chiang Mai", "Phuket", "Pattaya"],
        }

    def create_fake_user(self):
        phone_number = "+977" + self.fake.msisdn()[:8]
        full_name = self.fake.first_name() + " " + self.fake.last_name()

        gender = random.choice([GenderChoices.MALE, GenderChoices.FEMALE])
        profile_photo = self.generate_unsplash_url(query=f"{gender.lower()} portrait")
        zodiac_sign = random.choice([z for z in ZodiacSignChoices])
        mood = random.choice([m for m in MoodChoices])

        country = random.choice(list(self.country_city_map.keys()))
        city = random.choice(self.country_city_map[country])
        age = random.randint(18, 35)

        user = UserModel.objects.create(
            phone_number=phone_number,
            profile_photo=profile_photo,
            full_name=full_name,
            gender=gender,
            zodiac_sign=zodiac_sign,
            mood=mood,
            city=city,
            country=country,
            bio=self.fake.text(max_nb_chars=200),
            username=self.fake.user_name(),
            dob=self.fake.date_of_birth(minimum_age=18, maximum_age=35),
            is_profile_complete=True,
            is_phone_verified=True,
        )
        return user

    def handle(self, *args, **options):
        for _ in range(10):
            user = self.create_fake_user()
            self.stdout.write(self.style.SUCCESS(f"✅ Created user: {user.full_name} ({user.phone_number})"))

    def generate_unsplash_url(self, query="person", width=300, height=300):
        return f"https://source.unsplash.com/{width}x{height}/?{query}"