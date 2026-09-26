"""Isolated local test database; never connects to the configured application DB."""
from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "srisu.test_sqlite", "NAME": ":memory:"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "couple_feed": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "couple-feed-tests"},
}
