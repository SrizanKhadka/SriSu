#!/usr/bin/env python3
"""Run isolated Django checks with an in-memory DB/cache and no network."""
import argparse
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_LABELS = [
    "srisu.test_core", "srisu.test_api_routes", "authentication.test_auth",
    "chat.test_matrix", "chat.test_retirement", "chat.test_storage_purge",
    "chat.test_synapse_policy",
    "social.tests", "social.test_moments", "social.test_moment_replies",
    "social.test_couple_feed", "social.test_couple_profile",
]


def deny_network(*args, **kwargs):
    raise RuntimeError("Network disabled in SriSu workspace checks; use a dedicated integration environment.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "test", "migrations"])
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ["DJANGO_SETTINGS_MODULE"] = "srisu.workspace_test_settings"
    commands = {
        "check": ["check"],
        "test": ["test", *TEST_LABELS, "--noinput", "--verbosity", "1"],
        "migrations": ["makemigrations", "--check", "--dry-run"],
    }
    with patch.object(socket.socket, "connect", deny_network), \
         patch.object(socket.socket, "connect_ex", deny_network), \
         patch.object(socket.socket, "sendto", deny_network):
        from django.core.management import execute_from_command_line
        execute_from_command_line(["workspace", *commands[args.command]])


if __name__ == "__main__":
    main()
