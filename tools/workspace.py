#!/usr/bin/env python3
"""Run isolated Django checks with in-memory DB/cache/channels and no sockets."""
import argparse
import os
from pathlib import Path
import socket
import sys
import threading
from contextlib import contextmanager
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_LABELS = [
    "srisu.test_core", "srisu.test_api_routes", "authentication.test_auth",
    "social.tests", "social.test_moments", "social.test_moment_replies",
    "social.test_couple_feed", "social.test_couple_profile",
    "couple_chat.tests", "couple_chat.test_protocol", "couple_chat.test_media", "couple_chat.test_activity", "couple_chat.test_sparks",
]


def deny_network(*args, **kwargs):
    raise RuntimeError("Network disabled in SriSu workspace checks; use a dedicated integration environment.")


@contextmanager
def isolated_network():
    """Block egress, allowing only Windows' stdlib loopback socketpair for asyncio."""
    if os.name == "nt":
        # Twisted constructs its own loopback reactor waker on import. Initialize
        # this dependency before the guard; no Django apps or tests are imported.
        import daphne.server  # noqa: F401
    original_pair = socket.socketpair
    original_connect = socket.socket.connect
    local = threading.local()

    def pair(*args, **kwargs):
        local.creating_pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.creating_pair = False

    def connect(sock, address):
        if (os.name == "nt" and getattr(local, "creating_pair", False)
                and isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            return original_connect(sock, address)
        return deny_network()

    with patch.object(socket, "socketpair", pair), patch.object(socket.socket, "connect", connect), \
         patch.object(socket.socket, "connect_ex", deny_network), patch.object(socket.socket, "sendto", deny_network):
        yield


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
    with isolated_network():
        from django.core.management import execute_from_command_line
        execute_from_command_line(["workspace", *commands[args.command]])


if __name__ == "__main__":
    main()
