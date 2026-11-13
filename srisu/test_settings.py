"""Isolated local test database; never connects to the configured application DB."""
import os
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-disposable-tests-only-never-deploy")

from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "srisu.test_sqlite", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "couple_feed": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "couple-feed-tests"},
}
