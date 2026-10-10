"""Explicit isolated suites, optionally against the dedicated Docker test DB."""
import argparse
import os
from pathlib import Path
import sys

from workspace import TEST_LABELS, isolated_network

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--postgres", action="store_true")
parser.add_argument("--all", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
os.environ["DJANGO_SETTINGS_MODULE"] = "srisu.test_postgres_settings" if args.postgres else "srisu.workspace_test_settings"
if args.postgres:
    # Fixed to the separate, loopback-only, ephemeral test Compose service.
    os.environ["MOMENT_TEST_PG_PORT"] = "55449"
with isolated_network():
    from django.core.management import execute_from_command_line
    execute_from_command_line(["couple-chat-checks", "test", *(TEST_LABELS if args.all else ["couple_chat.tests", "couple_chat.test_protocol", "couple_chat.test_media", "couple_chat.test_activity", "couple_chat.test_sparks"]), "--noinput"])
