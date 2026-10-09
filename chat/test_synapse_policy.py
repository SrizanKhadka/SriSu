import asyncio
import importlib.util
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "deploy" / "synapse" / "srisu_room_policy.py"
RENDERER_PATH = ROOT / "deploy" / "synapse" / "render_config.py"
CONFIGURE_PATH = ROOT / "tools" / "configure_matrix_dev.py"


def load_policy_module():
    spec = importlib.util.spec_from_file_location("srisu_room_policy_test", POLICY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeModuleApi:
    def __init__(self):
        self.callbacks = {}
        self.resources = {}

    def register_spam_checker_callbacks(self, **callbacks):
        self.callbacks.update(callbacks)

    def register_web_resource(self, path, resource):
        self.resources[path] = resource


class FakeAlias:
    def __init__(self, value):
        self.value = value

    def to_string(self):
        return self.value


class SynapseRoomPolicyTests(SimpleTestCase):
    def setUp(self):
        self.module = load_policy_module()
        self.api = FakeModuleApi()
        self.policy = self.module.SriSuRoomPolicy(
            {
                "server_name": "matrix.test",
                "provisioning_localpart": "srisu_provisioner",
            },
            self.api,
        )
        self.provisioner = "@srisu_provisioner:matrix.test"
        self.room_config = {
            "visibility": "private",
            "room_version": "11",
            "room_alias_name": "srisu_chat_0123456789abcdef0123456789abcdef_e1",
            "invite": [
                "@srisu_user_1:matrix.test",
                "@srisu_user_2:matrix.test",
            ],
        }

    def run_async(self, awaitable):
        return asyncio.run(awaitable)

    def test_callbacks_are_registered_for_every_room_topology_escape_hatch(self):
        self.assertEqual(
            set(self.api.callbacks),
            {
                "user_may_create_room",
                "user_may_create_room_alias",
                "user_may_invite",
                "user_may_send_3pid_invite",
                "user_may_publish_room",
            },
        )

    def test_policy_version_probe_is_registered_and_non_secret(self):
        resource = self.api.resources[self.module.POLICY_ENDPOINT]

        class Request:
            def __init__(self):
                self.headers = {}

            def setHeader(self, name, value):
                self.headers[name] = value

        request = Request()
        payload = resource.render_GET(request).decode("utf-8")
        self.assertIn('"policy_version":"srisu-room-policy-v1"', payload)
        self.assertIn('"server_name":"matrix.test"', payload)
        self.assertNotIn("secret", payload.lower())
        self.assertEqual(request.headers[b"cache-control"], b"no-store")

    def test_only_provisioner_can_create_exact_deterministic_private_room(self):
        self.assertEqual(
            self.run_async(
                self.policy.user_may_create_room(
                    self.provisioner,
                    dict(self.room_config),
                )
            ),
            self.module.NOT_SPAM,
        )
        for user_id, changes in (
            ("@srisu_user_1:matrix.test", {}),
            (self.provisioner, {"visibility": "public"}),
            (self.provisioner, {"room_alias_name": "human-room"}),
            (self.provisioner, {"invite": ["@srisu_user_1:matrix.test"]}),
        ):
            with self.subTest(user_id=user_id, changes=changes):
                room_config = {**self.room_config, **changes}
                self.assertEqual(
                    self.run_async(
                        self.policy.user_may_create_room(user_id, room_config)
                    ),
                    self.module.Codes.FORBIDDEN,
                )

    def test_alias_and_invite_policy_denies_human_raw_operations(self):
        alias = FakeAlias(
            "#srisu_chat_0123456789abcdef0123456789abcdef_e1:matrix.test"
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_create_room_alias(self.provisioner, alias)
            ),
            self.module.NOT_SPAM,
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_create_room_alias(
                    "@srisu_user_1:matrix.test",
                    alias,
                )
            ),
            self.module.Codes.FORBIDDEN,
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_create_room_alias(
                    self.provisioner,
                    FakeAlias("#srisu_chat_not-a-uuid_e1:matrix.test"),
                )
            ),
            self.module.Codes.FORBIDDEN,
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_invite(
                    self.provisioner,
                    "@srisu_user_2:matrix.test",
                    "!room:matrix.test",
                )
            ),
            self.module.NOT_SPAM,
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_invite(
                    "@srisu_user_1:matrix.test",
                    "@srisu_user_2:matrix.test",
                    "!room:matrix.test",
                )
            ),
            self.module.Codes.FORBIDDEN,
        )

    def test_directory_and_third_party_invites_are_always_denied(self):
        self.assertEqual(
            self.run_async(
                self.policy.user_may_publish_room(
                    self.provisioner,
                    "!room:matrix.test",
                )
            ),
            self.module.Codes.FORBIDDEN,
        )
        self.assertEqual(
            self.run_async(
                self.policy.user_may_send_3pid_invite(
                    self.provisioner,
                    "email",
                    "synthetic@example.invalid",
                    "!room:matrix.test",
                )
            ),
            self.module.Codes.FORBIDDEN,
        )


class SynapseConfigRendererTests(SimpleTestCase):
    def test_renderer_wires_policy_module_and_exact_alias_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "homeserver.yaml"
            environment = {
                "SYNAPSE_CONFIG_PATH": str(output),
                "MATRIX_SERVER_NAME": "matrix.test",
                "MATRIX_HOMESERVER_URL": "http://127.0.0.1:8008",
                "MATRIX_JWT_SECRET": "j" * 40,
                "MATRIX_JWT_ISSUER": "srisu-test",
                "MATRIX_JWT_AUDIENCE": "srisu-matrix-test",
                "MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS": "300",
                "MATRIX_PROVISIONING_LOCALPART": "srisu_provisioner",
                "MATRIX_POSTGRES_DB": "matrix_test",
                "MATRIX_POSTGRES_USER": "matrix_test",
                "MATRIX_POSTGRES_PASSWORD": "p" * 20,
                "MATRIX_MACAROON_SECRET": "m" * 40,
                "MATRIX_FORM_SECRET": "f" * 40,
            }
            with patch.dict(os.environ, environment, clear=True):
                runpy.run_path(str(RENDERER_PATH), run_name="__main__")
            rendered = output.read_text(encoding="utf-8")

        self.assertIn("module: srisu_room_policy.SriSuRoomPolicy", rendered)
        self.assertIn('user_id: "@srisu_provisioner:matrix.test"', rendered)
        self.assertIn('alias: "#srisu_chat_*_e*:matrix.test"', rendered)
        self.assertIn("alias_creation_rules:", rendered)
        self.assertIn("enable_room_list_search: false", rendered)
        self.assertIn("room_list_publication_rules:", rendered)
        compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        start = (ROOT / "deploy" / "synapse" / "start-dev.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("PYTHONPATH: /srisu-config", compose)
        self.assertIn('export PYTHONPATH="/srisu-config', start)

    def test_configure_helper_preserves_existing_stable_server_name(self):
        spec = importlib.util.spec_from_file_location(
            "configure_matrix_dev_test",
            CONFIGURE_PATH,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            env_path = Path(temporary) / ".env"
            env_path.write_text(
                "DJANGO_DEBUG=true\n"
                "MATRIX_HOMESERVER_URL=http://127.0.0.1:8008\n"
                "MATRIX_SERVER_NAME=stable.matrix.test\n",
                encoding="utf-8",
            )
            with (
                patch.object(module, "ENV_PATH", env_path),
                patch.object(module, "ROOT", Path(temporary)),
                patch.object(sys, "argv", ["configure_matrix_dev.py"]),
            ):
                self.assertEqual(module.main(), 0)
            rendered = env_path.read_text(encoding="utf-8")
        self.assertIn("MATRIX_SERVER_NAME=stable.matrix.test", rendered)
        self.assertNotIn("MATRIX_SERVER_NAME=srisu.local", rendered)
