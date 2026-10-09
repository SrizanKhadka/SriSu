#!/usr/bin/env python3
"""Render a minimal private Synapse development config from container env."""

from __future__ import annotations

import json
import os
from pathlib import Path


OUTPUT = Path(os.environ.get("SYNAPSE_CONFIG_PATH", "/data/homeserver.yaml"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("replace-") or "\n" in value or "\r" in value:
        raise SystemExit(f"{name} must be set to a non-placeholder value")
    return value


def quoted(value: str) -> str:
    # JSON string literals are valid YAML and safely escape punctuation.
    return json.dumps(value)


def required_secret(name: str, minimum: int = 32) -> str:
    value = required(name)
    if len(value.encode("utf-8")) < minimum:
        raise SystemExit(f"{name} must contain at least {minimum} bytes")
    return value


server_name = required("MATRIX_SERVER_NAME")
public_base_url = required("MATRIX_HOMESERVER_URL").rstrip("/") + "/"
jwt_secret = required_secret("MATRIX_JWT_SECRET")
jwt_issuer = required("MATRIX_JWT_ISSUER")
jwt_audience = required("MATRIX_JWT_AUDIENCE")
postgres_database = required("MATRIX_POSTGRES_DB")
postgres_user = required("MATRIX_POSTGRES_USER")
postgres_password = required_secret("MATRIX_POSTGRES_PASSWORD", minimum=16)
macaroon_secret = required_secret("MATRIX_MACAROON_SECRET")
form_secret = required_secret("MATRIX_FORM_SECRET")
provisioning_localpart = required("MATRIX_PROVISIONING_LOCALPART")
try:
    access_token_lifetime = int(required("MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS"))
except ValueError:
    raise SystemExit("MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS must be an integer") from None
if not 300 <= access_token_lifetime <= 900:
    raise SystemExit("MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS must be between 300 and 900")

config = f"""\
server_name: {quoted(server_name)}
public_baseurl: {quoted(public_base_url)}
pid_file: /data/homeserver.pid
report_stats: false

modules:
  - module: srisu_room_policy.SriSuRoomPolicy
    config:
      server_name: {quoted(server_name)}
      provisioning_localpart: {quoted(provisioning_localpart)}

listeners:
  - port: 8008
    bind_addresses: ["0.0.0.0"]
    type: http
    tls: false
    x_forwarded: false
    resources:
      - names: [client]
        compress: true

database:
  name: psycopg2
  args:
    host: synapse_db
    port: 5432
    database: {quoted(postgres_database)}
    user: {quoted(postgres_user)}
    password: {quoted(postgres_password)}
    cp_min: 2
    cp_max: 10

log_config: /srisu-config/log.config
media_store_path: /data/media_store
signing_key_path: /data/homeserver.signing.key
macaroon_secret_key: {quoted(macaroon_secret)}
form_secret: {quoted(form_secret)}

# The listener exposes no federation resource and outbound federation is
# explicitly denied. This is a private single-homeserver development stack.
federation_domain_whitelist: []
send_federation: false
allow_profile_lookup_over_federation: false
allow_device_name_lookup_over_federation: false
allow_public_rooms_over_federation: false
trusted_key_servers: []
suppress_key_server_warning: true
# No human Matrix identity may publish an ad-hoc room. SriSu exposes only
# Django-authorized deterministic rooms, all of which remain directory-private.
room_list_publication_rules:
  - action: deny
alias_creation_rules:
  - user_id: {quoted(f"@{provisioning_localpart}:{server_name}")}
    alias: {quoted(f"#srisu_chat_*_e*:{server_name}")}
    action: allow
  - action: deny
enable_room_list_search: false

enable_registration: false
allow_guest_access: false
password_config:
  enabled: false
  localdb_enabled: false

jwt_config:
  enabled: true
  secret: {quoted(jwt_secret)}
  algorithm: HS256
  subject_claim: sub
  issuer: {quoted(jwt_issuer)}
  audiences:
    - {quoted(jwt_audience)}

# JWT is a replayable login credential during its short lifetime, and the
# resulting non-refreshable Matrix session is independently bounded.
nonrefreshable_access_token_lifetime: {access_token_lifetime}s
session_lifetime: {access_token_lifetime}s
login_via_existing_session:
  enabled: false

require_auth_for_profile_requests: true
limit_profile_requests_to_users_who_share_rooms: true
allow_public_rooms_without_auth: false
url_preview_enabled: false
presence:
  enabled: false
"""

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
temporary = OUTPUT.with_suffix(".yaml.tmp")
temporary.write_text(config, encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(OUTPUT)
