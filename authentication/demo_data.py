"""Deterministic, fictional identities and editorial content for the demo command."""
from datetime import date, timedelta
import random

WOMEN = "Asha Mira Nisha Anjali Sneha Riya Prakriti Sushma Kritika Pooja Sabina Deepa Srijana Binita Kabita Manisha Alina Sita Gita Sarita Pramila Rekha Samjhana Sujata Karuna Smriti Anisha Roshani Asmita Pratima Elina Sandhya Bimala Sunita Laxmi Sushmita Nirmala Rubina Anushka Ishika Diya Meera Tara Maya Aditi Kavya Leela Priya Sonia Amrita".split()
MEN = "Aarav Bikash Rohan Suman Pranav Aayush Anish Bibek Dipesh Kiran Milan Nabin Prakash Rajan Sanjay Saurav Ujjwal Yash Abhinav Aman Arun Ashish Bijay Bishal Deepak Dinesh Gaurav Hari Hemant Ishaan Jeevan Kamal Kushal Manoj Niraj Nischal Prabin Ramesh Roshan Sagar Samir Santosh Shishir Sudip Suraj Sushil Uttam Vikas Vivek Yogesh".split()
SURNAMES = "Thapa Gurung Shrestha Rai Karki Adhikari Basnet Poudel Tamang Sharma Joshi Bhandari Gautam Acharya Lama Raut Shah Magar Bista Pandey Koirala Ghimire Khadka Maharjan Pradhan Dangol Shakya Bajracharya Dhakal Neupane Pokhrel Aryal Regmi Sapkota Parajuli Bhattarai Subedi Khatri Chhetri Sherpa Limbu Chaudhary Yadav Mishra Singh Rana Upreti Kandel Tiwari Tripathi".split()
PLACES = (
    ("Kathmandu", "Nepal", "Asia/Kathmandu"), ("Pokhara", "Nepal", "Asia/Kathmandu"),
    ("Lalitpur", "Nepal", "Asia/Kathmandu"), ("Bhaktapur", "Nepal", "Asia/Kathmandu"),
    ("Chitwan", "Nepal", "Asia/Kathmandu"), ("Dharan", "Nepal", "Asia/Kathmandu"),
    ("Butwal", "Nepal", "Asia/Kathmandu"), ("London", "United Kingdom", "Europe/London"),
    ("Sydney", "Australia", "Australia/Sydney"), ("Toronto", "Canada", "America/Toronto"),
)
INTERESTS = ("Photography", "Travel", "Cooking", "Reading", "Hiking", "Music", "Cycling", "Art", "Coffee", "Movies", "Gardening", "Board games")
OCCUPATIONS = ("Product designer", "Teacher", "Architect", "Software engineer", "Illustrator", "Baker", "Researcher", "Community organiser", "Environmental scientist", "Bookshop owner")
BIO_ENDINGS = (
    "Usually planning our next weekend walk and a good place for breakfast.",
    "Collecting small adventures, handwritten notes, and recipes worth keeping.",
    "Happiest with a camera, a shared playlist, and nowhere urgent to be.",
    "Making room for slow mornings, good books, and time with my favourite person.",
    "Trying a new recipe every Sunday; the messy ones make the best stories.",
    "Always up for a scenic detour or an evening of board games with friends.",
    "Growing a balcony garden and a long list of places to explore together.",
    "A quiet cafe, a little music, and a conversation that lasts all afternoon.",
)
MEETINGS = (
    "a friend's birthday dinner", "a neighbourhood book exchange", "a weekend photography walk",
    "a community garden afternoon", "a live acoustic music night", "a pottery workshop",
    "a university reunion", "a volunteering event", "a small art exhibition", "a cooking class",
)
TAGLINES = (
    "Little adventures, a lot of laughter.", "Our favourite place is together.",
    "Slow mornings and a lifetime of stories.", "Making ordinary days worth remembering.",
    "A shared playlist and an open road.", "Growing together, one small kindness at a time.",
    "Home is the person beside you.", "Good food, long walks, and us.",
)
SONGS = (
    ("Hataarindai, Bataasindai", "Sajjan Raj Vaidya", ""),
    ("Sasto Mutu", "Sajjan Raj Vaidya", ""),
    ("Phool", "Sujan Chapagain", ""),
    ("Yellow", "", "Coldplay"),
    ("Better Together", "Jack Johnson", ""),
    ("Lover", "Taylor Swift", ""),
    ("Here Comes the Sun", "", "The Beatles"),
    ("Perfect", "Ed Sheeran", ""),
)
DREAMS = (
    "Take a month-long train journey together", "Build a home with a sunny reading corner",
    "Learn to make our families' favourite recipes", "Walk a new trail every season",
    "Create a photo book of our favourite ordinary days", "Grow a small kitchen garden",
    "Learn a new language together", "Host a dinner for all our closest friends",
)
AGE_RANGES = {"young-adult": (26, 29), "adult": (26, 38), "middle-aged": (44, 54), "older-adult": (58, 67)}


def zodiac(dob):
    # Month-day boundaries, including the Capricorn wrap across New Year.
    signs = ((1, 20, "AQUARIUS"), (2, 19, "PISCES"), (3, 21, "ARIES"),
             (4, 20, "TAURUS"), (5, 21, "GEMINI"), (6, 21, "CANCER"),
             (7, 23, "LEO"), (8, 23, "VIRGO"), (9, 23, "LIBRA"),
             (10, 23, "SCORPIO"), (11, 22, "SAGITTARIUS"), (12, 22, "CAPRICORN"))
    result = "CAPRICORN"
    for month, day, sign in signs:
        if (dob.month, dob.day) >= (month, day):
            result = sign
    return result


def definitions(seed, today, assets):
    rng = random.Random(seed)
    result = []
    for pair in range(50):
        city, country, zone = PLACES[pair % len(PLACES)]
        shared = rng.sample(INTERESTS, 3)
        for position, names in enumerate((WOMEN, MEN)):
            index = pair * 2 + position + 1
            first = names[pair]
            last = SURNAMES[(pair + position * 17) % len(SURNAMES)]
            band = assets[index - 1]["age_band"]
            age = rng.randint(*AGE_RANGES[band])
            dob = date(today.year - age, rng.randint(1, 12), rng.randint(1, 28))
            interests = shared + rng.sample([x for x in INTERESTS if x not in shared], 2)
            result.append(dict(
                index=index, pair=pair + 1, zone=zone, interests=interests,
                # Ofcom's fictional mobile range; these are never real subscriber IDs.
                phone_number=f"+447700900{index - 1:03d}",
                username=f"{first.lower()}.{last.lower()}.demo{index:03d}",
                email=f"account{index:03d}@demo.srisu.test", first_name=first, last_name=last,
                full_name=f"{first} {last}", gender="FEMALE" if position == 0 else "MALE",
                dob=dob, zodiac_sign=zodiac(dob), city=city, country=country,
                mood=("HAPPY", "GRATEFUL", "CALM", "ADVENTUROUS", "ROMANTIC")[index % 5],
                bio=f"{OCCUPATIONS[index % len(OCCUPATIONS)]} based in {city}. {BIO_ENDINGS[index % len(BIO_ENDINGS)]}",
                anniversary=today - timedelta(days=730 + pair * 17),
            ))
    return result
