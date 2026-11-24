#!/usr/bin/env python3
"""Safely prepare, start, and verify SriSu's local Docker backend.

This launcher is intentionally conservative. It never switches Git branches,
changes the Compose project name, removes containers or volumes, rotates existing
secrets, or bypasses the reviewed legacy-chat migration gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
INFRASTRUCTURE_SERVICES = ("srisu_db", "redis", "synapse_db", "synapse")
APPLICATION_SERVICES = ("web", "matrix_reconciler", "moment_video_worker")
CORE_SERVICES = (*APPLICATION_SERVICES, *INFRASTRUCTURE_SERVICES)
REQUIRED_SOURCE_FILES = (
    "manage.py",
    "docker-compose.yaml",
    ".env.example",
    "tools/configure_matrix_dev.py",
    "deploy/synapse/start-dev.sh",
    "chat/migrations/0005a_durable_matrix_revocation.py",
    "chat/migrations/0006_destroy_legacy_chat_transport.py",
    "social/management/commands/process_moment_videos.py",
    "social/migrations/0024_moment_video.py",
)
REQUIRED_CHAT_MIGRATION = "chat.0006_destroy_legacy_chat_transport"
PLACEHOLDER_PREFIXES = ("replace-", "change-me", "changeme")


class StartupError(RuntimeError):
    """A user-actionable local startup failure."""


@dataclass(frozen=True)
class RetainedState:
    project: str
    services: frozenset[str]
    volumes: frozenset[str]

    @property
    def has_app_database(self) -> bool:
        return "srisu_db" in self.services or "postgres_data" in self.volumes

    @property
    def has_matrix(self) -> bool:
        return bool(
            {"synapse", "synapse_db"} & self.services
            or {"synapse_postgres_data", "synapse_data"} & self.volumes
        )

    @property
    def has_any_persistent_state(self) -> bool:
        return self.has_app_database or self.has_matrix


@dataclass(frozen=True)
class SourceCheckout:
    branch: str
    commit: str

    @property
    def display_branch(self) -> str:
        return self.branch or "<detached>"


def _display_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run_command(
    command: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    quiet: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if not quiet:
        print(f"$ {_display_command(command)}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=capture,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise StartupError(
            f"Command timed out after {timeout} seconds: {_display_command(command)}"
        ) from error
    if check and result.returncode != 0:
        detail = ""
        if capture:
            combined = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
            if combined:
                detail = f"\n{combined[-3000:]}"
        raise StartupError(
            f"Command failed with exit code {result.returncode}: "
            f"{_display_command(command)}{detail}"
        )
    return result


def capture_command(command: list[str]) -> str:
    return run_command(command, capture=True, quiet=True).stdout.strip()


def validate_lan_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as error:
        raise StartupError(f"Invalid LAN IP address: {value!r}") from error
    if address.version != 4:
        raise StartupError("SriSu local mobile testing currently requires an IPv4 LAN address.")
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise StartupError(f"{address} is not a device-reachable LAN address.")
    if not address.is_private:
        raise StartupError(
            f"{address} is not a private LAN address. Pass the laptop's Wi-Fi/Ethernet IPv4."
        )
    return str(address)


def detect_lan_ip() -> str:
    if os.environ.get("WSL_DISTRO_NAME") or (
        Path("/proc/version").is_file()
        and "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    ):
        raise StartupError(
            "WSL's detected 172.x address is normally unreachable from a phone. Pass the Windows "
            "host Wi-Fi/Ethernet IPv4 explicitly with --lan-ip (find it with ipconfig)."
        )

    candidates: list[str] = []

    # A UDP connect chooses the host route without sending application data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass

    system = platform.system()
    commands: list[list[str]] = []
    if system == "Darwin":
        commands.extend(
            (["ipconfig", "getifaddr", "en0"], ["ipconfig", "getifaddr", "en1"])
        )
    elif system == "Windows":
        commands.append(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.IPAddress -notmatch '^(127\\.|169\\.254\\.)' -and "
                "$_.InterfaceAlias -match 'Wi-Fi|Ethernet' } | "
                "Select-Object -First 1 -ExpandProperty IPAddress)",
            ]
        )
    else:
        commands.extend(
            (["hostname", "-I"], ["sh", "-c", "ip route get 192.0.2.1 | sed -n 's/.* src \\([^ ]*\\).*/\\1/p'"])
        )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            candidates.extend(result.stdout.split())

    for candidate in candidates:
        try:
            return validate_lan_ip(candidate)
        except StartupError:
            continue
    raise StartupError(
        "Could not detect a private LAN IPv4. Re-run with --lan-ip, for example "
        "--lan-ip 192.168.1.50."
    )


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def replace_env_values(lines: list[str], updates: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                if key not in seen:
                    result.append(f"{key}={updates[key]}\n")
                    seen.add(key)
                continue
        result.append(line)
    remaining = {key: value for key, value in updates.items() if key not in seen}
    if remaining:
        if result and result[-1].strip():
            result.append("\n")
        result.append("# Managed by tools/start_local_server.py for local development.\n")
        result.extend(f"{key}={value}\n" for key, value in remaining.items())
    return result


def _is_placeholder(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    lowered = value.strip().lower()
    return lowered.startswith(PLACEHOLDER_PREFIXES)


def _write_env_atomic(lines: list[str]) -> None:
    # Concurrent worktrees may intentionally share one ignored .env via symlink.
    # Update its target atomically without replacing the link itself.
    destination = ENV_PATH.resolve() if ENV_PATH.is_symlink() else ENV_PATH
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.startup-", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.writelines(lines)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def prepare_environment(
    lan_ip: str, *, retained_matrix_state: bool = False
) -> tuple[bool, dict[str, str]]:
    created = not ENV_PATH.exists()
    if created:
        if not ENV_EXAMPLE_PATH.is_file():
            raise StartupError(f"Missing environment template: {ENV_EXAMPLE_PATH}")
        lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    current = parse_env(lines)
    if created:
        django_secret = secrets.token_urlsafe(64)
        postgres_password = secrets.token_urlsafe(36)
    else:
        if _is_placeholder(current.get("DJANGO_SECRET_KEY")):
            raise StartupError(
                "Existing .env has a missing/placeholder DJANGO_SECRET_KEY. Set a stable local "
                "secret manually; the launcher will not rotate an existing environment."
            )
        if _is_placeholder(current.get("POSTGRES_PASSWORD")):
            raise StartupError(
                "Existing .env has a missing/placeholder POSTGRES_PASSWORD. Set it manually "
                "before database initialization; the launcher will not rotate existing credentials."
            )
        django_secret = current["DJANGO_SECRET_KEY"]
        postgres_password = current["POSTGRES_PASSWORD"]
        if retained_matrix_state:
            required_matrix_values = (
                "MATRIX_SERVER_NAME",
                "MATRIX_JWT_SECRET",
                "MATRIX_POSTGRES_PASSWORD",
                "MATRIX_MACAROON_SECRET",
                "MATRIX_FORM_SECRET",
            )
            missing = [
                key for key in required_matrix_values if _is_placeholder(current.get(key))
            ]
            if missing:
                raise StartupError(
                    "Retained Synapse state exists, but .env is missing stable Matrix value(s): "
                    + ", ".join(missing)
                    + ". Restore the original .env; generating replacements would orphan the "
                    "existing Matrix database/identity."
                )

    updates = {
        "DJANGO_SECRET_KEY": django_secret,
        "DJANGO_DEBUG": "true",
        "DJANGO_ALLOWED_HOSTS": current.get("DJANGO_ALLOWED_HOSTS") or "localhost,127.0.0.1",
        "SRISU_DEV_LAN_HOST": lan_ip,
        "POSTGRES_DB": current.get("POSTGRES_DB") or "srisu_development",
        "POSTGRES_USER": current.get("POSTGRES_USER") or "srisu_development",
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_HOST": current.get("POSTGRES_HOST") or "127.0.0.1",
        "POSTGRES_PORT": current.get("POSTGRES_PORT") or "5433",
        "REDIS_HOST": current.get("REDIS_HOST") or "127.0.0.1",
        "REDIS_PORT": current.get("REDIS_PORT") or "6379",
        "OTP_MOCK_DELIVERY": current.get("OTP_MOCK_DELIVERY") or "true",
        "OTP_MOCK_CODE": current.get("OTP_MOCK_CODE") or "123456",
    }
    _write_env_atomic(replace_env_values(lines, updates))
    return created, parse_env(ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True))


def inspect_repository() -> SourceCheckout:
    missing = [relative for relative in REQUIRED_SOURCE_FILES if not (ROOT / relative).is_file()]
    if missing:
        raise StartupError(
            "This checkout is not compatible with the SriSu local backend launcher. "
            "Missing required source file(s): "
            + ", ".join(missing)
            + ". Select a backend branch containing the current Docker, Matrix, video Moments, and "
            "legacy-chat cutover implementation. The branch name itself is not restricted."
        )
    branch = capture_command(["git", "branch", "--show-current"])
    commit = capture_command(["git", "rev-parse", "HEAD"])
    source = SourceCheckout(branch=branch, commit=commit)
    print(f"Source checkout: {source.display_branch} @ {commit[:12]}", flush=True)
    status = capture_command(["git", "status", "--short"])
    if status:
        print("NOTICE: The worktree has local changes. They will be preserved.", flush=True)
    return source


def _default_compose_project_name() -> str:
    raw = os.environ.get("COMPOSE_PROJECT_NAME") or ROOT.name
    normalized = re.sub(r"[^a-z0-9_-]", "", raw.lower())
    normalized = re.sub(r"^[^a-z0-9]+", "", normalized)
    if not normalized:
        raise StartupError(f"Cannot derive a Docker Compose project name from {raw!r}.")
    return normalized


def inspect_retained_state(docker: str) -> RetainedState:
    """Inspect persistent state scoped to this checkout's Compose project."""
    explicit_container = run_command(
        [
            docker,
            "ps",
            "-a",
            "--filter",
            "name=^/srisuDB$",
            "--format",
            "{{.ID}}",
        ],
        capture=True,
        quiet=True,
        timeout=10,
    ).stdout.strip()
    project = _default_compose_project_name()
    if explicit_container:
        inspected_project = run_command(
            [
                docker,
                "inspect",
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
                explicit_container.splitlines()[0],
            ],
            capture=True,
            quiet=True,
            timeout=10,
        ).stdout.strip()
        if inspected_project:
            project = inspected_project

    services = frozenset(
        run_command(
            [
                docker,
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                '{{.Label "com.docker.compose.service"}}',
            ],
            capture=True,
            quiet=True,
            timeout=10,
        ).stdout.split()
    )
    volume_names = run_command(
        [
            docker,
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ],
        capture=True,
        quiet=True,
        timeout=10,
    ).stdout.split()
    logical_volumes: set[str] = set()
    for volume_name in volume_names:
        logical_name = run_command(
            [
                docker,
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "com.docker.compose.volume" }}',
                volume_name,
            ],
            capture=True,
            quiet=True,
            timeout=10,
        ).stdout.strip()
        if logical_name:
            logical_volumes.add(logical_name)
    return RetainedState(project, services, frozenset(logical_volumes))


def assert_new_environment_safe(state: RetainedState) -> None:
    """Refuse new credentials when surviving state may need the original ones."""
    if state.has_any_persistent_state:
        details = []
        if state.services:
            details.append(f"service container(s) exist: {', '.join(sorted(state.services))}")
        if state.volumes:
            details.append(f"persistent volume(s) exist: {', '.join(sorted(state.volumes))}")
        raise StartupError(
            f"Cannot create a new .env for Compose project {state.project!r} because "
            + " and ".join(details)
            + ". Restore the original ignored .env so its PostgreSQL and Matrix credentials "
            "continue to match retained data. No credentials were generated."
        )


def _docker_ready(docker: str) -> bool:
    try:
        result = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _wait_for_docker(docker: str, wait_seconds: int) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _docker_ready(docker):
            return True
        time.sleep(2)
    return False


def ensure_docker(wait_seconds: int) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise StartupError("Docker CLI was not found. Install Docker Desktop/Engine with Compose v2.")
    if _docker_ready(docker):
        return docker

    try:
        context_result = subprocess.run(
            [docker, "context", "show"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise StartupError("Docker context inspection timed out.") from error
    context = context_result.stdout.strip()
    system = platform.system()
    started = False
    if context == "colima" and shutil.which("colima"):
        print("Docker is stopped; starting the existing Colima context...", flush=True)
        run_command([shutil.which("colima") or "colima", "start"])
        started = True
    elif system == "Darwin" and Path("/Applications/Docker.app").exists():
        print("Docker is stopped; opening Docker Desktop...", flush=True)
        run_command(["open", "-a", "Docker"])
        started = True
    elif system == "Windows":
        docker_desktop = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
        if docker_desktop.is_file():
            print("Docker is stopped; opening Docker Desktop...", flush=True)
            subprocess.Popen([str(docker_desktop)], cwd=ROOT)
            started = True

    if not started:
        raise StartupError(
            f"Docker daemon is not reachable on context {context or '<unknown>'}. Start Docker "
            "Desktop/Engine, keep the intended context selected, and retry."
        )
    if not _wait_for_docker(docker, wait_seconds):
        raise StartupError(f"Docker did not become ready within {wait_seconds} seconds.")
    return docker


def compose(docker: str, *arguments: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return run_command([docker, "compose", *arguments], capture=capture)


def wait_for_command(command: list[str], description: str, wait_seconds: int) -> None:
    deadline = time.monotonic() + wait_seconds
    last_output = ""
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=min(15, wait_seconds),
            )
        except subprocess.TimeoutExpired:
            last_output = "command timed out"
            time.sleep(2)
            continue
        if result.returncode == 0:
            print(f"Ready: {description}", flush=True)
            return
        last_output = (result.stderr or result.stdout).strip()
        time.sleep(2)
    detail = f" Last output: {last_output[-1000:]}" if last_output else ""
    raise StartupError(f"Timed out waiting for {description}.{detail}")


def wait_for_http(url: str, description: str, wait_seconds: int, require_json: bool = False) -> None:
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"User-Agent": "SriSu-local-startup/1"})
            with opener.open(request, timeout=5) as response:
                content_type = response.headers.get("Content-Type", "")
                if response.status == 200 and (not require_json or "json" in content_type.lower()):
                    print(f"Ready: {description} ({url})", flush=True)
                    return
                last_error = f"HTTP {response.status}, Content-Type {content_type!r}"
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = str(error)
        time.sleep(2)
    raise StartupError(f"Timed out waiting for {description}: {last_error}")


def validate_notification_environment(values: dict[str, str]) -> None:
    if values.get("NOTIFICATIONS_ENABLED", "").lower() != "true":
        raise StartupError(
            "--with-notifications requires NOTIFICATIONS_ENABLED=true in .env. "
            "Leave the optional worker off unless Firebase delivery is intentionally configured."
        )
    configured_path = values.get("SRISU_FIREBASE_ADC_PATH", "./.local/firebase-adc.json")
    adc_path = Path(configured_path)
    if not adc_path.is_absolute():
        adc_path = ROOT / adc_path
    if not adc_path.is_file() or not os.access(adc_path, os.R_OK):
        raise StartupError(
            f"--with-notifications requires a readable Firebase ADC file: {adc_path}"
        )


def validate_runtime_capabilities(docker: str) -> None:
    """Verify chat/video commands and encoders before stopping apps or migrating."""
    compose(docker, "run", "--rm", "--no-deps", "web", "python", "manage.py", "check")
    compose(
        docker,
        "run",
        "--rm",
        "--no-deps",
        "web",
        "python",
        "manage.py",
        "help",
        "matrix_status",
    )
    compose(
        docker,
        "run",
        "--rm",
        "--no-deps",
        "web",
        "python",
        "manage.py",
        "help",
        "reconcile_matrix_rooms",
    )
    migration_plan = compose(
        docker,
        "run",
        "--rm",
        "--no-deps",
        "web",
        "python",
        "manage.py",
        "showmigrations",
        "chat",
        "--plan",
        capture=True,
    ).stdout
    if REQUIRED_CHAT_MIGRATION not in migration_plan:
        raise StartupError(
            "The built web image does not contain the required chat cutover migration "
            f"{REQUIRED_CHAT_MIGRATION}. Rebuild from a compatible backend checkout without "
            "--skip-build; "
            "no application services were stopped and no migrations were run."
        )
    compose(
        docker, "run", "--rm", "--no-deps", "moment_video_worker",
        "python", "manage.py", "help", "process_moment_videos",
    )
    for binary in ("ffmpeg", "ffprobe"):
        compose(
            docker, "run", "--rm", "--no-deps", "moment_video_worker",
            binary, "-version", capture=True,
        )


def dry_run_plan(lan_ip: str, *, pull: bool, skip_build: bool, with_notifications: bool) -> None:
    print("DRY RUN - no files, containers, branches, or databases will be changed.")
    print(f"LAN IP: {lan_ip}")
    commands: list[list[str]] = [
        [sys.executable, "tools/configure_matrix_dev.py", "--public-base-url", f"http://{lan_ip}:8008"],
        ["docker", "compose", "config", "--quiet"],
    ]
    if pull:
        commands.append(["docker", "compose", "pull", *INFRASTRUCTURE_SERVICES])
    if not skip_build:
        commands.append(["docker", "compose", "build", "web"])
    commands.extend(
        [
            ["docker", "compose", "up", "-d", "srisu_db", "redis", "synapse_db"],
            ["docker", "compose", "up", "-d", "--force-recreate", "synapse"],
            ["docker", "compose", "run", "--rm", "--no-deps", "web", "python", "manage.py", "check"],
            ["docker", "compose", "run", "--rm", "--no-deps", "web", "python", "manage.py", "help", "matrix_status"],
            ["docker", "compose", "run", "--rm", "--no-deps", "web", "python", "manage.py", "help", "reconcile_matrix_rooms"],
            ["docker", "compose", "run", "--rm", "--no-deps", "web", "python", "manage.py", "showmigrations", "chat", "--plan"],
            ["docker", "compose", "run", "--rm", "--no-deps", "moment_video_worker", "python", "manage.py", "help", "process_moment_videos"],
            ["docker", "compose", "run", "--rm", "--no-deps", "moment_video_worker", "ffmpeg", "-version"],
            ["docker", "compose", "run", "--rm", "--no-deps", "moment_video_worker", "ffprobe", "-version"],
            ["docker", "compose", "stop", *APPLICATION_SERVICES],
            ["docker", "compose", "run", "--rm", "web", "python", "manage.py", "migrate", "--noinput"],
            ["docker", "compose", "up", "-d", "--force-recreate", *APPLICATION_SERVICES],
            ["docker", "compose", "exec", "-T", "moment_video_worker", "python", "manage.py", "check"],
        ]
    )
    if with_notifications:
        commands.append(
            [
                "docker",
                "compose",
                "--profile",
                "notifications",
                "up",
                "-d",
                "--force-recreate",
                "notification_worker",
            ]
        )
    commands.extend(
        [
            ["docker", "compose", "exec", "-T", "web", "python", "manage.py", "check"],
            ["docker", "compose", "exec", "-T", "web", "python", "manage.py", "migrate", "--check"],
            ["docker", "compose", "exec", "-T", "web", "python", "manage.py", "reconcile_matrix_rooms", "--limit", "100"],
            ["docker", "compose", "exec", "-T", "web", "python", "manage.py", "matrix_status", "--require-ready"],
            ["docker", "compose", "ps"],
        ]
    )
    for command in commands:
        print(f"$ {_display_command(command)}")
    print(f"HTTP checks: http://{lan_ip}:8000/api/auth/interests/")
    print(f"HTTP checks: http://{lan_ip}:8008/_matrix/client/versions")


def start_server(args: argparse.Namespace) -> None:
    lan_ip = validate_lan_ip(args.lan_ip) if args.lan_ip else detect_lan_ip()
    print(f"Using LAN IP: {lan_ip}", flush=True)

    if args.dry_run:
        dry_run_plan(
            lan_ip,
            pull=args.pull,
            skip_build=args.skip_build,
            with_notifications=args.with_notifications,
        )
        return

    source = inspect_repository()
    docker = ensure_docker(args.wait_seconds)
    retained_state = inspect_retained_state(docker)
    if not ENV_PATH.exists():
        assert_new_environment_safe(retained_state)
    created, values = prepare_environment(
        lan_ip, retained_matrix_state=retained_state.has_matrix
    )
    print(
        "Created a private ignored .env with fresh local secrets."
        if created
        else "Preserved existing .env secrets and updated only local development host settings.",
        flush=True,
    )
    if created:
        print(
            "NOTICE: A genuinely empty database will stop at the reviewed chat.0006 cutover gate. "
            "Complete docs/chat-v2.md's one-time readiness/backup sequence, then rerun this launcher.",
            flush=True,
        )

    configure_command = [
        sys.executable,
        str(ROOT / "tools" / "configure_matrix_dev.py"),
        "--public-base-url",
        f"http://{lan_ip}:8008",
    ]
    run_command(configure_command)
    values = parse_env(ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True))
    if args.with_notifications:
        validate_notification_environment(values)

    compose(docker, "version")
    compose(docker, "config", "--quiet")
    services = set(compose(docker, "config", "--services", capture=True).stdout.split())
    missing = set(CORE_SERVICES) - services
    if missing:
        raise StartupError(f"Compose is missing required services: {', '.join(sorted(missing))}")
    if args.with_notifications:
        profile_services = set(
            compose(
                docker,
                "--profile",
                "notifications",
                "config",
                "--services",
                capture=True,
            ).stdout.split()
        )
        if "notification_worker" not in profile_services:
            raise StartupError("The notifications profile is missing notification_worker.")

    if args.pull:
        compose(docker, "pull", *INFRASTRUCTURE_SERVICES)
    if not args.skip_build:
        compose(docker, "build", "web")

    compose(docker, "up", "-d", "srisu_db", "redis", "synapse_db")
    compose(docker, "up", "-d", "--force-recreate", "synapse")
    wait_for_command(
        [
            docker,
            "compose",
            "exec",
            "-T",
            "srisu_db",
            "sh",
            "-c",
            'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        ],
        "Django PostgreSQL",
        args.wait_seconds,
    )
    wait_for_command(
        [docker, "compose", "exec", "-T", "redis", "redis-cli", "ping"],
        "Redis",
        args.wait_seconds,
    )
    wait_for_http(
        f"http://{lan_ip}:8008/_matrix/client/versions",
        "Synapse",
        args.wait_seconds,
        require_json=True,
    )
    validate_runtime_capabilities(docker)

    status_prefix = ("--profile", "notifications") if args.with_notifications else ()
    running_services = set(
        compose(
            docker,
            *status_prefix,
            "ps",
            "--status",
            "running",
            "--services",
            capture=True,
        ).stdout.split()
    )

    # Quiesce the application processes before changing database schema. This is
    # idempotent and does not remove containers, data, or named volumes.
    compose(docker, "stop", *APPLICATION_SERVICES)
    if args.with_notifications:
        compose(docker, "--profile", "notifications", "stop", "notification_worker")

    try:
        compose(
            docker,
            "run",
            "--rm",
            "web",
            "python",
            "manage.py",
            "migrate",
            "--noinput",
        )
    except StartupError as error:
        restore = [
            service
            for service in APPLICATION_SERVICES
            if service in running_services
        ]
        if restore:
            print(
                "Migration failed; restarting the application services that were running before "
                "this attempt...",
                flush=True,
            )
            compose(docker, "start", *restore)
        if args.with_notifications and "notification_worker" in running_services:
            compose(docker, "--profile", "notifications", "start", "notification_worker")
        raise StartupError(
            f"{error}\n\nMigration stopped safely. If output mentions "
            "DESTROY_LEGACY_CHAT_TRANSPORT_V1, do not bypass it here. Follow the reviewed "
            "readiness and backup sequence in docs/chat-v2.md, then rerun this launcher."
        ) from error

    compose(docker, "up", "-d", "--force-recreate", *APPLICATION_SERVICES)
    wait_for_command(
        [docker, "compose", "exec", "-T", "moment_video_worker", "python", "manage.py", "check"],
        "Moment video worker",
        args.wait_seconds,
    )
    if args.with_notifications:
        compose(
            docker,
            "--profile",
            "notifications",
            "up",
            "-d",
            "--force-recreate",
            "notification_worker",
        )
        wait_for_command(
            [
                docker,
                "compose",
                "--profile",
                "notifications",
                "exec",
                "-T",
                "notification_worker",
                "python",
                "-c",
                "import os,time; assert time.time()-os.path.getmtime('/tmp/srisu-notification-worker.heartbeat') < 120",
            ],
            "notification worker heartbeat",
            args.wait_seconds,
        )

    wait_for_http(
        f"http://{lan_ip}:8000/api/auth/interests/",
        "Django API",
        args.wait_seconds,
        require_json=True,
    )
    compose(docker, "exec", "-T", "web", "python", "manage.py", "check")
    compose(docker, "exec", "-T", "web", "python", "manage.py", "migrate", "--check")
    compose(docker, "exec", "-T", "web", "python", "manage.py", "reconcile_matrix_rooms", "--limit", "100")
    wait_for_command(
        [
            docker,
            "compose",
            "exec",
            "-T",
            "web",
            "python",
            "manage.py",
            "matrix_status",
            "--require-ready",
        ],
        "Matrix rooms and live policy verification",
        args.wait_seconds,
    )
    compose(docker, "ps")

    print("\nSriSu local backend is ready.")
    print(f"Source checkout:  {source.display_branch} @ {source.commit[:12]}")
    print(f"Django API:      http://{lan_ip}:8000")
    print(f"Matrix/Synapse:  http://{lan_ip}:8008")
    print("\nUse these ignored frontend local.properties values, then rebuild/reinstall the app:")
    print(f"srisu.apiBaseUrl=http://{lan_ip}:8000/")
    print("srisu.environment=development")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lan-ip",
        help="Laptop LAN IPv4 reachable by the phone/simulator; auto-detected when omitted.",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Refresh the pinned PostgreSQL, Redis, and Synapse images before startup.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Reuse the existing local Django image instead of rebuilding it.",
    )
    parser.add_argument(
        "--with-notifications",
        action="store_true",
        help="Also start notification_worker after validating enabled Firebase ADC configuration.",
    )
    parser.add_argument(
        "--allow-other-branch",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="Maximum wait for Docker and services (default: 120).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the safe plan without reading/writing repo state or invoking Docker.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_seconds < 10:
        print("ERROR: --wait-seconds must be at least 10.", file=sys.stderr)
        return 2
    try:
        start_server(args)
    except KeyboardInterrupt:
        print("\nStartup interrupted. Existing containers and volumes were left untouched.", file=sys.stderr)
        return 130
    except StartupError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        print(
            "Inspect with: docker compose logs --tail 100 web matrix_reconciler moment_video_worker synapse",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
