from django.apps import AppConfig


class CoupleChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "couple_chat"

    def ready(self):
        from . import signals  # noqa: F401
