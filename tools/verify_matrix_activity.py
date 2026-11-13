#!/usr/bin/env python3
"""Verify presence/typing with synthetic users on a disposable loopback Synapse.

Uses the project's pinned Docker image and room policy, a temporary SQLite DB,
and random test-only JWT secrets. Never reads .env or contacts the running stack.
Run with .venv/bin/python tools/verify_matrix_activity.py. Requires Docker + PyJWT.
"""
from pathlib import Path
import http.client
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import jwt

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/element-hq/synapse:v1.162.0"


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def main():
    container = "srisu-activity-check-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="srisu-activity-", dir=ROOT / ".venv") as directory:
        data = Path(directory)
        config = data / "homeserver.yaml"
        secret = secrets.token_hex(32)
        env = {**os.environ, "SYNAPSE_CONFIG_PATH": str(config),
               "MATRIX_SERVER_NAME": "activity.test", "MATRIX_HOMESERVER_URL": "http://localhost:8008",
               "MATRIX_JWT_SECRET": secret, "MATRIX_JWT_ISSUER": "activity-test",
               "MATRIX_JWT_AUDIENCE": "activity-test", "MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS": "900",
               "MATRIX_PROVISIONING_LOCALPART": "srisu_provisioner", "MATRIX_POSTGRES_DB": "unused",
               "MATRIX_POSTGRES_USER": "unused", "MATRIX_POSTGRES_PASSWORD": secrets.token_hex(32),
               "MATRIX_MACAROON_SECRET": secrets.token_hex(32), "MATRIX_FORM_SECRET": secrets.token_hex(32)}
        subprocess.run([os.sys.executable, str(ROOT / "deploy/synapse/render_config.py")], env=env, check=True)
        value = config.read_text()
        assert "presence:\n  enabled: true" in value
        value = re.sub(r"database:\n.*?\nlog_config:", "database:\n  name: sqlite3\n  args:\n    database: /data/activity.db\n\nlog_config:", value, flags=re.S)
        config.write_text(value)
        try:
            run("docker", "run", "--detach", "--name", container, "--user", "0:0",
                "-p", "127.0.0.1::8008", "-v", f"{data}:/data", "-v", f"{ROOT / 'deploy/synapse'}:/srisu-config:ro",
                "-e", "PYTHONPATH=/srisu-config", "--entrypoint", "python", IMAGE,
                "-m", "synapse.app.homeserver", "--config-path", "/data/homeserver.yaml")
            address = run("docker", "port", container, "8008/tcp")
            base = "http://" + address

            def api(method, path, token=None, body=None, expected=200):
                request = urllib.request.Request(base + path, method=method,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
                for attempt in range(6):
                    try:
                        with urllib.request.urlopen(request, timeout=12) as response:
                            status, content = response.status, response.read()
                    except urllib.error.HTTPError as error:
                        status, content = error.code, error.read()
                    result = json.loads(content) if content else {}
                    if status == 429 and expected != 429 and attempt < 5:
                        time.sleep(min(30, max(.1, result.get("retry_after_ms", 1000) / 1000)))
                        continue
                    assert status == expected, f"{method} {path.split('?')[0]}: HTTP {status}, expected {expected}"
                    return result

            for attempt in range(80):
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1):
                        break
                except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected):
                    if run("docker", "inspect", "--format", "{{.State.Running}}", container) != "true":
                        diagnostic = subprocess.check_output(["docker", "logs", container], stderr=subprocess.STDOUT, text=True)[-2500:]
                        raise RuntimeError("Disposable Synapse exited: " + diagnostic)
                    time.sleep(.5)
            else:
                raise RuntimeError("Disposable Synapse failed to start")

            def login(name):
                proof = jwt.encode({"sub": name, "iss": "activity-test", "aud": "activity-test", "exp": int(time.time()) + 600}, secret, algorithm="HS256")
                result = api("POST", "/_matrix/client/v3/login", body={"type": "org.matrix.login.jwt", "token": proof})
                return result["user_id"], result["access_token"]

            provisioner, provisioner_token = login("srisu_provisioner")
            alice, alice_token = login("srisu_user_1")
            bob, bob_token = login("srisu_user_2")
            outsider, outsider_token = login("srisu_user_3")
            room = api("POST", "/_matrix/client/v3/createRoom", provisioner_token, {
                "visibility": "private", "room_version": "11", "preset": "private_chat",
                "room_alias_name": "srisu_chat_" + uuid.uuid4().hex + "_e1", "invite": [alice, bob],
                "initial_state": [{"type": "m.room.encryption", "state_key": "", "content": {"algorithm": "m.megolm.v1.aes-sha2"}}],
            })["room_id"]
            quote = lambda value: urllib.parse.quote(value, safe="")
            for token in (alice_token, bob_token):
                api("POST", f"/_matrix/client/v3/rooms/{quote(room)}/join", token, {})
            api("POST", f"/_matrix/client/v3/rooms/{quote(room)}/leave", provisioner_token, {})
            presence = f"/_matrix/client/v3/presence/{quote(bob)}/status"
            for state in ("online", "unavailable", "offline"):
                api("PUT", presence, bob_token, {"presence": state})
                assert api("GET", presence, alice_token)["presence"] == state
            api("GET", presence, outsider_token, expected=403)
            api("PUT", presence, alice_token, {"presence": "online"}, expected=403)
            print("PASS presence: online/away/offline; unrelated read and identity spoofing denied", flush=True)

            since = api("GET", "/_matrix/client/v3/sync?timeout=0", alice_token)["next_batch"]
            typing_path = f"/_matrix/client/v3/rooms/{quote(room)}/typing/{quote(bob)}"

            def expect_typing(typing):
                nonlocal since
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    response = api("GET", "/_matrix/client/v3/sync?timeout=1000&since=" + quote(since), alice_token)
                    since = response["next_batch"]
                    events = response.get("rooms", {}).get("join", {}).get(room, {}).get("ephemeral", {}).get("events", [])
                    for event in events:
                        if event.get("type") == "m.typing" and (bob in event["content"]["user_ids"]) == typing:
                            return
                raise AssertionError(f"Missing typing={typing} event")

            api("PUT", typing_path, bob_token, {"typing": True, "timeout": 10000})
            expect_typing(True)
            api("PUT", typing_path, bob_token, {"typing": False})
            expect_typing(False)
            api("PUT", typing_path, bob_token, {"typing": True, "timeout": 1000})
            expect_typing(True)
            expect_typing(False)
            # Synapse 1.162.0 deliberately returns HTTP 400 for typing identity spoofing.
            rejected = api("PUT", typing_path, alice_token, {"typing": True, "timeout": 10000}, expected=400)
            assert rejected["error"] == "Cannot set another user's typing state"
            api("PUT", f"/_matrix/client/v3/rooms/{quote(room)}/typing/{quote(outsider)}", outsider_token,
                {"typing": True, "timeout": 10000}, expected=403)
            print("PASS typing: partner sync event, explicit stop, server expiry; spoofing and non-member writes denied", flush=True)
            response = api("GET", "/_matrix/client/v3/sync?timeout=0", outsider_token)
            assert room not in response.get("rooms", {}).get("join", {})
            assert all(event.get("sender") != bob for event in response.get("presence", {}).get("events", []))
            print("PASS unrelated user sync contains neither couple room nor partner presence", flush=True)
        finally:
            subprocess.run(["docker", "rm", "--force", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
