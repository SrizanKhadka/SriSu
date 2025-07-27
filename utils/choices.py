from django.db.models import TextChoices


class GenderChoices(TextChoices):
    MALE = "MALE", "Male"
    FEMALE = "FEMALE", "Female"


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

class MessageType(TextChoices):
    TEXT = "TEXT", "Text"
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"
    AUDIO = "AUDIO","Audio"


class MessageReaction(TextChoices):
    HAHA = "HAHA", "Haha"
    WOW = "WOW", "Wow"
    SAD = "SAD", "Sad"
    ANGRY = "ANGRY", "Angry"
    LOVE = "Love", "Love"


class DeleteOption(TextChoices):
    DELETED = "DELETED","deleted"
    NOT_DELETED = "NOT_DELETED", "Not deleted"
    DELETE_FOR_ME = "DELETE_FOR_ME", "Delete for me"
    DELETE_FOR_EVERYONE = "DELETE_FOR_EVERYONE", "Delete for everyone"
    CONVERSATION_DELETED = "CONVERSATION_DELETED", "Conversation deleted"

class ChatTypeChoices(TextChoices):
    SINGLE = "single", "Single"
    COUPLE = "couple", "Couple"