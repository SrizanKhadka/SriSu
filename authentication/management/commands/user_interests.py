from faker import Faker
from utils.choices import *
from django.core.management.base import BaseCommand
from authentication.models import UserInterestModel, UserModel, InterestCategory, InterestModel
import random
class Command(BaseCommand):
    help = "Seed dummy users and their interests into the database"

    def __init__(self):
        super().__init__()
        self.fake = Faker()

    def create_interest_categories(self):
        categories = [
            ("SPORTS", "Sports"),
            ("MUSIC", "Music"),
            ("TRAVEL", "Travel"),
            ("FOOD", "Food"),
            ("ART", "Art"),
            ("TECHNOLOGY", "Technology"),
            ("FITNESS", "Fitness"),
            ("FASHION", "Fashion"),
            ("GAMING", "Gaming"),
            ("MOVIES", "Movies"),
            ("BOOKS", "Books"),
            ("PHOTOGRAPHY", "Photography")
        ]

        for name, label in categories:
            category, created = InterestCategory.objects.get_or_create(name=name, label=label)
            if created:
                self.stdout.write(self.style.SUCCESS(f"✅ Created interest category: {name}"))
    
    def create_interest_models(self):
        categories = {cat.name: cat for cat in InterestCategory.objects.all()}
        if not categories:
            self.create_interest_categories()
            categories = {cat.name: cat for cat in InterestCategory.objects.all()}

        # Map interests to their correct category key
        interest_mapping = {
                    # SPORTS
                    "Badminton": "SPORTS",
                    "Baseball": "SPORTS",
                    "Basketball": "SPORTS",
                    "Cycling": "SPORTS",
                    "Football": "SPORTS",
                    "Hockey": "SPORTS",

                    # MUSIC
                    "Jazz Music": "MUSIC",
                    "Music Production": "MUSIC",
                    "Pop Music": "MUSIC",
                    "Rock Music": "MUSIC",

                    # TRAVEL
                    "Hiking": "TRAVEL",
                    "Traveling": "TRAVEL",

                    # FOOD
                    "Baking": "FOOD",
                    "Cooking": "FOOD",

                    # ART
                    "Digital Art": "ART",
                    "Graphic Design": "ART",
                    "Painting": "ART",
                    "Sculpting": "ART",

                    # PHOTOGRAPHY
                    "Photography": "PHOTOGRAPHY",

                    # TECHNOLOGY
                    "AI Technology": "TECHNOLOGY",
                    "Blockchain": "TECHNOLOGY",
                    "Cloud Computing": "TECHNOLOGY",
                    "Cybersecurity": "TECHNOLOGY",
                    
                    # FITNESS
                    "Fitness Training": "FITNESS",
                    "Yoga": "FITNESS",

                    # FASHION
                    "Fashion Design": "FASHION",

                    # GAMING
                    "Video Games": "GAMING",

                    # MOVIES
                    "Movies": "MOVIES",

                    # BOOKS
                    "Books": "BOOKS",
                    "Reading": "BOOKS",
                    "Writing": "BOOKS",
                }


        for interest_name, category_key in interest_mapping.items():
            category = categories.get(category_key)
            if category:
                interest, created = InterestModel.objects.get_or_create(
                    name=interest_name,
                    category=category
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"✅ Created interest '{interest_name}' under category '{category.label}'")
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(f"⚠ Category '{category_key}' not found for interest '{interest_name}'")
                )


    def create_fake_user_interests(self):
        categories = list(InterestCategory.objects.all())
        users = UserModel.objects.all()

        for user in users:
            # pick a random subset of categories for this user
            chosen_categories = random.sample(categories, k=random.randint(2, 5))
            for category in chosen_categories:
                obj, created = UserInterestModel.objects.get_or_create(
                    user=user,
                    category=category
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✅ Added interest '{category.label}' for user: {user.full_name} ({user.phone_number})"
                        )
                    )

    def handle(self, *args, **options):
        self.create_interest_models()


            