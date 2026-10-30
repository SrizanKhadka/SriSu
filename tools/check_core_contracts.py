#!/usr/bin/env python3
"""Validate the authoritative core-1 schemas and deterministic wire fixtures."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1] / 'contracts/core-1'
SCHEMA = json.loads((ROOT / 'schema.json').read_text())


def validate_contract(name, payload):
    Draft202012Validator({**SCHEMA, '$ref': '#/$defs/' + name}, format_checker=FormatChecker()).validate(payload)


def main():
    Draft202012Validator.check_schema(SCHEMA)
    fixtures = json.loads((ROOT / 'fixtures.json').read_text())
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for name, definition in manifest['fixtures'].items():
        validate_contract(definition, fixtures[name])
    print(f'core-1: schema and {len(fixtures)} shared fixtures validated')


if __name__ == '__main__': main()
