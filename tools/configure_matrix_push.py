#!/usr/bin/env python3
"""Enable the private Matrix notification feed in an existing ignored environment."""
import argparse
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlparse

from configure_matrix_dev import _parse_env, _replace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--url", default="http://web:8000")
    args = parser.parse_args()
    lines = args.env.read_text(encoding="utf-8").splitlines(keepends=True)
    values = _parse_env(lines)
    if any(values.get(key, "").lower() != "true" for key in ("MATRIX_ENABLED", "NOTIFICATIONS_ENABLED")):
        raise SystemExit("Enable and verify Matrix and the notification worker before configuring chat push.")
    parsed = urlparse(args.url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise SystemExit("The application-service URL must be an explicit internal HTTP(S) origin.")
    token = values.get("MATRIX_PUSH_HS_TOKEN") or secrets.token_urlsafe(48)
    if len(token) < 32 or "\n" in token or "\r" in token:
        raise SystemExit("Existing Matrix push credential is invalid; refusing to replace it.")
    try:
        cutoff = int(values.get("MATRIX_CHAT_PUSH_NOT_BEFORE_MS", "0"))
    except ValueError:
        raise SystemExit("Existing Matrix push cutoff must be an integer.") from None
    if cutoff <= 0 or values.get("MATRIX_CHAT_PUSH_ENABLED", "false").lower() != "true":
        cutoff = int(time.time() * 1000)
    updates = {"MATRIX_CHAT_PUSH_ENABLED": "true", "MATRIX_PUSH_AS_URL": args.url.rstrip("/"),
        "MATRIX_PUSH_HS_TOKEN": token,
        "MATRIX_CHAT_PUSH_NOT_BEFORE_MS": str(cutoff)}
    descriptor, temporary = tempfile.mkstemp(prefix=".env.matrix-push-", dir=args.env.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("".join(_replace(lines, updates)))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, args.env)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Matrix chat push configured. Credentials remain only in the local environment.")
    print("Apply additive notification migrations, then recreate web, notification_worker and Synapse.")


if __name__ == "__main__":
    main()
