from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("start_local_server.py")
SPEC = importlib.util.spec_from_file_location("start_local_server", MODULE_PATH)
assert SPEC and SPEC.loader
startup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = startup
SPEC.loader.exec_module(startup)

CONFIGURE_PATH = Path(__file__).with_name("configure_matrix_dev.py")
CONFIGURE_SPEC = importlib.util.spec_from_file_location("configure_matrix_dev", CONFIGURE_PATH)
assert CONFIGURE_SPEC and CONFIGURE_SPEC.loader
configure_matrix = importlib.util.module_from_spec(CONFIGURE_SPEC)
sys.modules[CONFIGURE_SPEC.name] = configure_matrix
CONFIGURE_SPEC.loader.exec_module(configure_matrix)


class LocalServerStartupTests(unittest.TestCase):
    def _create_compatible_source_tree(self, root: Path) -> None:
        for relative in startup.REQUIRED_SOURCE_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def test_validate_lan_ip_accepts_private_ipv4(self) -> None:
        self.assertEqual(startup.validate_lan_ip("192.168.100.179"), "192.168.100.179")
        self.assertEqual(startup.validate_lan_ip("10.0.0.5"), "10.0.0.5")

    def test_validate_lan_ip_rejects_unsafe_addresses(self) -> None:
        for value in ("127.0.0.1", "169.254.2.3", "0.0.0.0", "not-an-ip", "2001:db8::1"):
            with self.subTest(value=value), self.assertRaises(startup.StartupError):
                startup.validate_lan_ip(value)

    def test_existing_environment_preserves_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            example_path = root / ".env.example"
            env_path.write_text(
                "DJANGO_SECRET_KEY=stable-django\n"
                "POSTGRES_PASSWORD=stable-postgres\n"
                "DJANGO_DEBUG=false\n"
                "SRISU_DEV_LAN_HOST=192.168.1.10\n",
                encoding="utf-8",
            )
            example_path.write_text("", encoding="utf-8")
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "ENV_PATH", env_path
            ), mock.patch.object(startup, "ENV_EXAMPLE_PATH", example_path):
                created, values = startup.prepare_environment("192.168.100.179")
            self.assertFalse(created)
            self.assertEqual(values["DJANGO_SECRET_KEY"], "stable-django")
            self.assertEqual(values["POSTGRES_PASSWORD"], "stable-postgres")
            self.assertEqual(values["DJANGO_DEBUG"], "true")
            self.assertEqual(values["SRISU_DEV_LAN_HOST"], "192.168.100.179")
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_new_environment_replaces_local_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            example_path = root / ".env.example"
            example_path.write_text(
                "DJANGO_SECRET_KEY=replace-with-a-new-random-secret\n"
                "POSTGRES_PASSWORD=replace-locally\n",
                encoding="utf-8",
            )
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "ENV_PATH", env_path
            ), mock.patch.object(startup, "ENV_EXAMPLE_PATH", example_path):
                created, values = startup.prepare_environment("10.1.2.3")
            self.assertTrue(created)
            self.assertFalse(startup._is_placeholder(values["DJANGO_SECRET_KEY"]))
            self.assertFalse(startup._is_placeholder(values["POSTGRES_PASSWORD"]))
            self.assertEqual(values["SRISU_DEV_LAN_HOST"], "10.1.2.3")
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_existing_placeholder_environment_is_not_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            example_path = root / ".env.example"
            original = "DJANGO_SECRET_KEY=replace-me\nPOSTGRES_PASSWORD=stable-postgres\n"
            env_path.write_text(original, encoding="utf-8")
            example_path.write_text("", encoding="utf-8")
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "ENV_PATH", env_path
            ), mock.patch.object(startup, "ENV_EXAMPLE_PATH", example_path):
                with self.assertRaises(startup.StartupError):
                    startup.prepare_environment("192.168.1.50")
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_duplicate_managed_keys_are_canonicalized(self) -> None:
        lines = [
            "SRISU_DEV_LAN_HOST=192.168.1.10\n",
            "OTHER=value\n",
            "SRISU_DEV_LAN_HOST=192.168.1.11\n",
        ]
        result = startup.replace_env_values(
            lines, {"SRISU_DEV_LAN_HOST": "192.168.100.179"}
        )
        self.assertEqual(result.count("SRISU_DEV_LAN_HOST=192.168.100.179\n"), 1)
        self.assertNotIn("192.168.1.11", "".join(result))

    def test_retained_matrix_state_requires_stable_matrix_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            example_path = root / ".env.example"
            env_path.write_text(
                "DJANGO_SECRET_KEY=stable-django\n"
                "POSTGRES_PASSWORD=stable-postgres\n"
                "MATRIX_SERVER_NAME=srisu.local\n"
                "MATRIX_JWT_SECRET=replace-with-new-secret\n",
                encoding="utf-8",
            )
            example_path.write_text("", encoding="utf-8")
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "ENV_PATH", env_path
            ), mock.patch.object(startup, "ENV_EXAMPLE_PATH", example_path):
                with self.assertRaises(startup.StartupError) as context:
                    startup.prepare_environment(
                        "192.168.1.50", retained_matrix_state=True
                    )
            self.assertIn("Retained Synapse state exists", str(context.exception))

    @unittest.skipIf(os.name == "nt", "symlink creation requires elevated Windows privileges")
    def test_shared_environment_symlink_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared.env"
            link = root / ".env"
            example_path = root / ".env.example"
            shared.write_text(
                "DJANGO_SECRET_KEY=stable-django\nPOSTGRES_PASSWORD=stable-postgres\n",
                encoding="utf-8",
            )
            link.symlink_to(shared)
            example_path.write_text("", encoding="utf-8")
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "ENV_PATH", link
            ), mock.patch.object(startup, "ENV_EXAMPLE_PATH", example_path):
                startup.prepare_environment("192.168.1.50")
            self.assertTrue(link.is_symlink())
            self.assertIn("SRISU_DEV_LAN_HOST=192.168.1.50", shared.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "symlink creation requires elevated Windows privileges")
    def test_matrix_configuration_preserves_shared_environment_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared.env"
            link = root / ".env"
            shared.write_text(
                "DJANGO_DEBUG=true\n"
                "MATRIX_HOMESERVER_URL=http://192.168.1.10:8008\n"
                "MATRIX_SERVER_NAME=srisu.local\n",
                encoding="utf-8",
            )
            link.symlink_to(shared)
            with mock.patch.object(configure_matrix, "ROOT", root), mock.patch.object(
                configure_matrix, "ENV_PATH", link
            ), mock.patch.object(
                sys,
                "argv",
                [
                    "configure_matrix_dev.py",
                    "--public-base-url",
                    "http://192.168.1.50:8008",
                ],
            ):
                self.assertEqual(configure_matrix.main(), 0)
            self.assertTrue(link.is_symlink())
            updated = shared.read_text(encoding="utf-8")
            self.assertIn("MATRIX_HOMESERVER_URL=http://192.168.1.50:8008", updated)
            self.assertIn("MATRIX_SERVER_NAME=srisu.local", updated)

    def test_matrix_configuration_canonicalizes_duplicate_keys(self) -> None:
        lines = [
            "MATRIX_HOMESERVER_URL=http://192.168.1.10:8008\n",
            "OTHER=value\n",
            "MATRIX_HOMESERVER_URL=http://192.168.1.11:8008\n",
        ]
        result = configure_matrix._replace(
            lines, {"MATRIX_HOMESERVER_URL": "http://192.168.1.50:8008"}
        )
        self.assertEqual(
            result.count("MATRIX_HOMESERVER_URL=http://192.168.1.50:8008\n"), 1
        )
        self.assertNotIn("192.168.1.11", "".join(result))

    def test_notification_worker_requires_enabled_flag_and_file(self) -> None:
        with self.assertRaises(startup.StartupError):
            startup.validate_notification_environment({"NOTIFICATIONS_ENABLED": "false"})
        with tempfile.TemporaryDirectory() as directory:
            adc = Path(directory) / "firebase.json"
            adc.write_text("{}", encoding="utf-8")
            startup.validate_notification_environment(
                {
                    "NOTIFICATIONS_ENABLED": "true",
                    "SRISU_FIREBASE_ADC_PATH": str(adc),
                }
            )

    def test_missing_env_is_rejected_when_project_state_survives(self) -> None:
        state = startup.RetainedState(
            "srisu-backend",
            frozenset({"srisu_db", "synapse"}),
            frozenset({"postgres_data", "synapse_data"}),
        )
        with self.assertRaises(startup.StartupError) as context:
            startup.assert_new_environment_safe(state)
        self.assertIn("Restore the original ignored .env", str(context.exception))

    def test_unrelated_project_volume_does_not_block_fresh_environment(self) -> None:
        state = startup.RetainedState("srisu-backend", frozenset(), frozenset())
        startup.assert_new_environment_safe(state)

    def test_retained_synapse_volume_is_rejected_without_environment(self) -> None:
        state = startup.RetainedState(
            "srisu-backend", frozenset(), frozenset({"synapse_postgres_data"})
        )
        with self.assertRaises(startup.StartupError):
            startup.assert_new_environment_safe(state)

    def test_dry_run_plan_contains_no_destructive_commands(self) -> None:
        with mock.patch("builtins.print") as output:
            startup.dry_run_plan(
                "192.168.100.179",
                pull=True,
                skip_build=False,
                with_notifications=False,
            )
        rendered = "\n".join(" ".join(str(part) for part in call.args) for call in output.call_args_list)
        for forbidden in (
            "down -v",
            "--volumes",
            "volume prune",
            "DESTROY_LEGACY_CHAT_TRANSPORT_V1",
            "purge_legacy_chat_storage",
            "git reset",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("manage.py migrate --noinput", rendered)
        self.assertIn("matrix_status --require-ready", rendered)
        self.assertIn("--force-recreate synapse", rendered)

    def test_repository_inspection_accepts_any_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._create_compatible_source_tree(root)
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup,
                "capture_command",
                side_effect=[
                    "dev-core-architecture",
                    "0123456789abcdef0123456789abcdef01234567",
                    "",
                ],
            ), mock.patch("builtins.print") as output:
                startup.inspect_repository()
            rendered = "\n".join(
                " ".join(str(part) for part in call.args)
                for call in output.call_args_list
            )
            self.assertIn("dev-core-architecture @ 0123456789ab", rendered)

    def test_repository_inspection_rejects_missing_capabilities_before_git_or_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manage.py").write_text("", encoding="utf-8")
            with mock.patch.object(startup, "ROOT", root), mock.patch.object(
                startup, "capture_command"
            ) as capture, mock.patch.object(startup, "ensure_docker") as ensure_docker:
                with self.assertRaisesRegex(startup.StartupError, "not compatible"):
                    startup.start_server(self._startup_args())
            capture.assert_not_called()
            ensure_docker.assert_not_called()

    def _startup_args(self) -> SimpleNamespace:
        return SimpleNamespace(
            lan_ip="192.168.100.179",
            dry_run=False,
            pull=False,
            skip_build=False,
            with_notifications=False,
            allow_other_branch=False,
            wait_seconds=30,
        )

    def test_mocked_happy_path_orders_runtime_changes_safely(self) -> None:
        calls = []

        def fake_compose(_docker, *arguments, capture=False):
            calls.append(arguments)
            if arguments == ("config", "--services"):
                return mock.Mock(stdout="\n".join(startup.CORE_SERVICES))
            if arguments == ("ps", "--status", "running", "--services"):
                return mock.Mock(stdout="web\nmatrix_reconciler\n")
            if arguments == (
                "run", "--rm", "--no-deps", "web", "python", "manage.py",
                "showmigrations", "chat", "--plan"
            ):
                return mock.Mock(stdout="[X] chat.0006_destroy_legacy_chat_transport\n")
            return mock.Mock(stdout="")

        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DJANGO_SECRET_KEY=x\nPOSTGRES_PASSWORD=y\n", encoding="utf-8")
            with mock.patch.object(startup, "ENV_PATH", env_path), mock.patch.object(
                startup,
                "inspect_repository",
                return_value=startup.SourceCheckout("dev-core-architecture", "0" * 40),
            ), mock.patch.object(startup, "ensure_docker", return_value="docker"), mock.patch.object(
                startup,
                "inspect_retained_state",
                return_value=startup.RetainedState("srisu", frozenset(), frozenset()),
            ), mock.patch.object(
                startup,
                "prepare_environment",
                return_value=(False, {"DJANGO_SECRET_KEY": "x", "POSTGRES_PASSWORD": "y"}),
            ), mock.patch.object(startup, "run_command"), mock.patch.object(
                startup, "compose", side_effect=fake_compose
            ), mock.patch.object(startup, "wait_for_command"), mock.patch.object(
                startup, "wait_for_http"
            ):
                startup.start_server(self._startup_args())

        migration = ("run", "--rm", "web", "python", "manage.py", "migrate", "--noinput")
        self.assertLess(calls.index(("up", "-d", "--force-recreate", "synapse")), calls.index(migration))
        preflight = (
            "run", "--rm", "--no-deps", "web", "python", "manage.py",
            "showmigrations", "chat", "--plan"
        )
        self.assertLess(calls.index(preflight), calls.index(("stop", "web", "matrix_reconciler")))
        self.assertLess(calls.index(("stop", "web", "matrix_reconciler")), calls.index(migration))
        self.assertIn(("up", "-d", "--force-recreate", "web", "matrix_reconciler"), calls)

    def test_stale_web_image_is_rejected_before_services_stop_or_migrate(self) -> None:
        calls = []

        def fake_compose(_docker, *arguments, capture=False):
            calls.append(arguments)
            if arguments == ("config", "--services"):
                return mock.Mock(stdout="\n".join(startup.CORE_SERVICES))
            return mock.Mock(stdout="")

        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DJANGO_SECRET_KEY=x\nPOSTGRES_PASSWORD=y\n", encoding="utf-8")
            with mock.patch.object(startup, "ENV_PATH", env_path), mock.patch.object(
                startup,
                "inspect_repository",
                return_value=startup.SourceCheckout("any-branch", "1" * 40),
            ), mock.patch.object(startup, "ensure_docker", return_value="docker"), mock.patch.object(
                startup,
                "inspect_retained_state",
                return_value=startup.RetainedState("srisu", frozenset(), frozenset()),
            ), mock.patch.object(
                startup,
                "prepare_environment",
                return_value=(False, {"DJANGO_SECRET_KEY": "x", "POSTGRES_PASSWORD": "y"}),
            ), mock.patch.object(startup, "run_command"), mock.patch.object(
                startup, "compose", side_effect=fake_compose
            ), mock.patch.object(startup, "wait_for_command"), mock.patch.object(
                startup, "wait_for_http"
            ):
                with self.assertRaisesRegex(startup.StartupError, "does not contain"):
                    startup.start_server(self._startup_args())

        self.assertNotIn(("stop", "web", "matrix_reconciler"), calls)
        self.assertFalse(any("migrate" in call for call in calls))

    def test_mocked_migration_failure_restores_previously_running_services(self) -> None:
        calls = []

        def fake_compose(_docker, *arguments, capture=False):
            calls.append(arguments)
            if arguments == ("config", "--services"):
                return mock.Mock(stdout="\n".join(startup.CORE_SERVICES))
            if arguments == ("ps", "--status", "running", "--services"):
                return mock.Mock(stdout="web\nmatrix_reconciler\n")
            if arguments == (
                "run", "--rm", "--no-deps", "web", "python", "manage.py",
                "showmigrations", "chat", "--plan"
            ):
                return mock.Mock(stdout="[X] chat.0006_destroy_legacy_chat_transport\n")
            if arguments == (
                "run", "--rm", "web", "python", "manage.py", "migrate", "--noinput"
            ):
                raise startup.StartupError("migration gate")
            return mock.Mock(stdout="")

        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DJANGO_SECRET_KEY=x\nPOSTGRES_PASSWORD=y\n", encoding="utf-8")
            with mock.patch.object(startup, "ENV_PATH", env_path), mock.patch.object(
                startup,
                "inspect_repository",
                return_value=startup.SourceCheckout("dev-core-architecture", "0" * 40),
            ), mock.patch.object(startup, "ensure_docker", return_value="docker"), mock.patch.object(
                startup,
                "inspect_retained_state",
                return_value=startup.RetainedState("srisu", frozenset(), frozenset()),
            ), mock.patch.object(
                startup,
                "prepare_environment",
                return_value=(False, {"DJANGO_SECRET_KEY": "x", "POSTGRES_PASSWORD": "y"}),
            ), mock.patch.object(startup, "run_command"), mock.patch.object(
                startup, "compose", side_effect=fake_compose
            ), mock.patch.object(startup, "wait_for_command"), mock.patch.object(
                startup, "wait_for_http"
            ):
                with self.assertRaises(startup.StartupError):
                    startup.start_server(self._startup_args())

        self.assertIn(("start", "web", "matrix_reconciler"), calls)


if __name__ == "__main__":
    unittest.main()
