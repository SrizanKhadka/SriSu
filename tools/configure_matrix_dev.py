#!/usr/bin/env python3
"""Configure the ignored local .env for the private development Matrix stack."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _parse_env(lines: list[str]) -> dict[str, str]:
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _replace(lines: list[str], updates: dict[str, str]) -> list[str]:
    remaining = dict(updates)
    result = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}\n")
                continue
        result.append(line)
    if remaining:
        if result and result[-1].strip():
            result.append("\n")
        result.append("# Generated private Matrix development configuration.\n")
        result.extend(f"{key}={value}\n" for key, value in remaining.items())
    return result


def _secret(current: str | None) -> str:
    if current and not current.startswith("replace-"):
        return current
    return secrets.token_urlsafe(48)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--public-base-url",
        help="Mobile-reachable Synapse origin, for example http://192.168.1.73:8008",
    )
    parser.add_argument(
        "--server-name",
        help=(
            "Stable Matrix identity domain. Omit to preserve MATRIX_SERVER_NAME "
            "from the existing .env (or use srisu.local only when absent)."
        ),
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        raise SystemExit("Create .env first: cp .env.example .env")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    current = _parse_env(lines)
    base_url = args.public_base_url or current.get("MATRIX_HOMESERVER_URL", "")
    parsed = urlparse(base_url)
    try:
        parsed.port
    except ValueError:
        raise SystemExit("--public-base-url contains an invalid port") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SystemExit("--public-base-url must be an http(s) origin reachable by the app")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SystemExit("--public-base-url must not contain a path, query, or fragment")
    if parsed.scheme == "http" and current.get("DJANGO_DEBUG", "").lower() != "true":
        raise SystemExit(
            "HTTP Matrix development requires an explicit DJANGO_DEBUG=true in .env; "
            "production-like configuration must use HTTPS"
        )
    server_name = (
        args.server_name
        or current.get("MATRIX_SERVER_NAME")
        or "srisu.local"
    )
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", server_name):
        raise SystemExit("--server-name must be a stable Matrix server name")

    updates = {
        "MATRIX_ENABLED": "true",
        "MATRIX_SERVICE_CLASS": "chat.services.matrix_provisioning.SynapseMatrixService",
        "MATRIX_HOMESERVER_URL": base_url.rstrip("/"),
        "MATRIX_INTERNAL_HOMESERVER_URL": "http://synapse:8008",
        "MATRIX_SERVER_NAME": server_name,
        "MATRIX_PROTOCOL_ID": "matrix-e2ee-v1",
        "MATRIX_SESSION_TTL_SECONDS": "120",
        "MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS": "300",
        "MATRIX_PROVISIONING_LOCALPART": "srisu_provisioner",
        "MATRIX_ROOM_VERSION": "11",
        "MATRIX_JWT_SECRET": _secret(current.get("MATRIX_JWT_SECRET")),
        "MATRIX_JWT_ALGORITHM": "HS256",
        "MATRIX_JWT_ISSUER": "srisu-django",
        "MATRIX_JWT_AUDIENCE": "srisu-matrix",
        "MATRIX_HTTP_TIMEOUT_SECONDS": "5",
        "MATRIX_POSTGRES_DB": current.get("MATRIX_POSTGRES_DB") or "srisu_matrix",
        "MATRIX_POSTGRES_USER": current.get("MATRIX_POSTGRES_USER") or "srisu_matrix",
        "MATRIX_POSTGRES_PASSWORD": _secret(current.get("MATRIX_POSTGRES_PASSWORD")),
        "MATRIX_MACAROON_SECRET": _secret(current.get("MATRIX_MACAROON_SECRET")),
        "MATRIX_FORM_SECRET": _secret(current.get("MATRIX_FORM_SECRET")),
    }
    updated = "".join(_replace(lines, updates))
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.matrix-", dir=ROOT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, ENV_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print(f"Configured private Matrix development at {base_url.rstrip('/')}")
    print("Secrets were written only to ignored .env; recreate Docker services next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
