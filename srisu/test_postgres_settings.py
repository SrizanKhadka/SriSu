"""Dedicated disposable PostgreSQL cluster, separate from application credentials."""
import os
from .workspace_test_settings import *  # noqa: F403

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "postgres",
    "USER": "postgres",
    "HOST": "127.0.0.1",
    "PORT": os.environ.get("MOMENT_TEST_PG_PORT", "55439"),
    "TEST": {"NAME": "test_couple_moments"},
}}
