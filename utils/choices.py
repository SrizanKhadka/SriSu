from django.db.models import TextChoices


class GenderChoices(TextChoices):
    MALE = "MALE", "Male"
    FEMALE = "FEMALE", "Female"

class RelationshipStatusChoices(TextChoices):
    SINGLE = "SINGLE", "Single"
    MINGLED = "MINGLED", "Mingled"


class ZodiacSignChoices(TextChoices):
    ARIES = "ARIES", "Aries"
    TAURUS = "TAURUS", "Taurus"
    GEMINI = "GEMINI", "Gemini"
    CANCER = "CANCER", "Cancer"
    LEO = "LEO", "Leo"
    VIRGO = "VIRGO", "Virgo"
    LIBRA = "LIBRA", "Libra"
    SCORPIO = "SCORPIO", "Scorpio"
    SAGITTARIUS = "SAGITTARIUS", "Sagittarius"
    CAPRICORN = "CAPRICORN", "Capricorn"
    AQUARIUS = "AQUARIUS", "Aquarius"
    PISCES = "PISCES", "Pisces"


class MoodChoices(TextChoices):
    HAPPY = "HAPPY", "Happy"
    ROMANTIC = "ROMANTIC", "Romantic"
    EXCITED = "EXCITED", "Excited"
    CALM = "CALM", "Calm"
    ADVENTUROUS = "ADVENTUROUS", "Adventurous"
    PLAYFUL = "PLAYFUL", "Playful"
    GRATEFUL = "GRATEFUL", "Grateful"
    THOUGHTFUL = "THOUGHTFUL", "Thoughtful"
    SUPPORTIVE = "SUPPORTIVE", "Supportive"
    IN_LOVE = "IN_LOVE", "In Love"
    MISSING = "MISSING", "Missing"
    CELEBRATORY = "CELEBRATORY", "Celebratory"


class OtpStatusChoices(TextChoices):
    NOTHING = "NOTHING", "nothing"
    NEW = "NEW", "new"
    EXPIRED = "EXPIRED", "expired"


class CoupleConnectionStatus(TextChoices):
    NOTHING = "NOTHING", "Nothing"
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    BREAKUP = "BREAK-UP", "Break-Up"

class SingleConnectionStatus(TextChoices):
    NOTHING = "NOTHING", "Nothing"
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    BLOCKED = "BLOCKED", "Blocked"

class InterestCategoryChoices(TextChoices):
    SPORTS = "SPORTS", "Sports"
    MUSIC = "MUSIC", "Music"
    TRAVEL = "TRAVEL", "Travel"
    FOOD = "FOOD", "Food"
    ART = "ART", "Art"
    TECHNOLOGY = "TECHNOLOGY", "Technology"
    FITNESS = "FITNESS", "Fitness"
    FASHION = "FASHION", "Fashion"
    GAMING = "GAMING", "Gaming"
    MOVIES = "MOVIES", "Movies"
    BOOKS = "BOOKS", "Books"
    PHOTOGRAPHY = "PHOTOGRAPHY", "Photography"
    NATURE = "NATURE", "Nature"
    ANIMALS = "ANIMALS", "Animals"
    HOBBIES = "HOBBIES", "Hobbies"
    CULTURE = "CULTURE", "Culture"
    EDUCATION = "EDUCATION", "Education"
    OTHER = "OTHER", "Other"


class MomentVisibility(TextChoices):
    PRIVATE = "private", "Private"
    PUBLIC = "public", "Public"
    FRIENDS = "friends", "Friends"


class MomentMood(TextChoices):
    HAPPY = "happy", "Happy"
    ROMANTIC = "romantic", "Romantic"
    FUNNY = "funny", "Funny"
    EMOTIONAL = "emotional", "Emotional"
    ADVENTURE = "adventure", "Adventure"
    GRATEFUL = "grateful", "Grateful"
    SPECIAL = "special", "Special"
