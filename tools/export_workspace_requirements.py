#!/usr/bin/env python3
"""Export existing lock versions for local tests; no dependency upgrades.

Use psycopg2-binary at the same version for local wheels (no system libpq build).
Production manifests are unchanged. This export pins versions, not artifact hashes.
"""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "Pipfile.lock").read_text())
print("# Generated from Pipfile.lock for local workspace checks only.")
print("# psycopg2 is replaced by psycopg2-binary at the same locked version.")
for name, spec in sorted(lock["default"].items()):
    version = spec["version"]
    if not version.startswith("=="):
        raise ValueError("Expected an exact locked version for " + name)
    if name == "psycopg2":
        name = "psycopg2-binary"
    extras = "[" + ",".join(spec["extras"]) + "]" if spec.get("extras") else ""
    marker = "; " + spec["markers"] if spec.get("markers") else ""
    print(name + extras + version + marker)
