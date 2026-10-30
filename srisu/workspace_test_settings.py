"""Disposable local checks. Never use this settings module to serve the app."""
import atexit
import os
import shutil
import tempfile

# Override inherited environment and .env credentials before base settings load.
for key, value in {
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_AUTH_TOKEN": "workspace-test-placeholder",
    "TWILIO_PHONE_NUMBER": "+15005550006",
    "POSTGRES_DB": "unused_workspace_tests",
    "POSTGRES_USER": "unused_workspace_tests",
    "POSTGRES_PASSWORD": "unused_workspace_tests",
    "POSTGRES_HOST": "127.0.0.1",
    "COUPLE_FEED_ENABLED": "true",
    "SRISU_CONFIRM_DESTROY_LEGACY_CHAT": "DESTROY_LEGACY_CHAT_TRANSPORT_V1",
}.items():
    os.environ[key] = value

from .test_settings import *  # noqa: E402,F403

SECRET_KEY = "insecure-srisu-disposable-workspace-tests-only"
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = tempfile.mkdtemp(prefix="srisu-workspace-tests-")
atexit.register(shutil.rmtree, MEDIA_ROOT, ignore_errors=True)

# Keep tests quiet while retaining sanitized failure diagnostics.
LOGGING["loggers"]["srisu"]["level"] = "WARNING"
