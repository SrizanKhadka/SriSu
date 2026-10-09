#!/usr/bin/env python3
"""Run the explicit workspace suites in a disposable loopback PostgreSQL cluster."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

from workspace import TEST_LABELS

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    args = parser.parse_args()
    binaries = args.postgres_bin.resolve()
    for name in ("initdb", "pg_ctl"):
        if not (binaries / name).is_file():
            parser.error(f"Missing PostgreSQL executable: {name}")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="srisu-auth-pg-") as temporary:
        cluster = Path(temporary) / "data"
        log = Path(temporary) / "postgres.log"
        subprocess.run([str(binaries / "initdb"), "-D", str(cluster), "-U", "postgres",
                        "-A", "trust", "--no-locale", "-E", "UTF8"], check=True, stdout=subprocess.DEVNULL)
        command = [str(binaries / "pg_ctl"), "-D", str(cluster)]
        subprocess.run([*command, "-l", str(log), "-o",
                        f"-h 127.0.0.1 -p {port} -k {temporary}", "-w", "start"], check=True)
        try:
            os.chdir(ROOT)
            sys.path.insert(0, str(ROOT))
            os.environ["DJANGO_SETTINGS_MODULE"] = "srisu.test_postgres_settings"
            os.environ["MOMENT_TEST_PG_PORT"] = str(port)
            import django
            django.setup()
            from django.conf import settings
            from django.db import connections
            from django.test.utils import get_runner
            settings.MEDIA_ROOT = str(Path(temporary) / "media")
            settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
            # Native libpq uses only the explicit disposable DB configuration above.
            # All Python socket egress is forbidden.
            def denied(*args, **kwargs):
                raise RuntimeError("Network forbidden in isolated PostgreSQL tests")
            with patch.object(socket.socket, "connect", denied), \
                 patch.object(socket.socket, "connect_ex", denied), \
                 patch.object(socket.socket, "sendto", denied):
                failures = get_runner(settings)(verbosity=1, interactive=False).run_tests(TEST_LABELS)
            connections.close_all()
            return bool(failures)
        finally:
            subprocess.run([*command, "-m", "immediate", "-w", "stop"], check=True)


if __name__ == "__main__":
    sys.exit(main())
