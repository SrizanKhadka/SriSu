"""Historical schema only; Matrix owns all runtime chat transport."""
from django.apps import AppConfig

class CoupleChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "couple_chat"
