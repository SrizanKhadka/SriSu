"""Create the complete 100-account development dataset without outbound traffic."""
import json
from pathlib import Path
import tempfile

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from authentication.demo_accounts import DATASET, DemoAccounts
from authentication.otp import mock_code


class Command(BaseCommand):
    help = "Seed 100 complete demo users, 50 couples, local photos, stories, moments and plans."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-local", action="store_true", help="Confirm this is a local/disposable development database.")
        parser.add_argument("--dry-run", action="store_true", help="Validate the environment, assets and collisions without writing anything.")
        parser.add_argument("--seed", type=int, default=42, help="Deterministic content seed; reuse this value on subsequent runs (default: 42).")
        parser.add_argument("--refresh-activity", action="store_true", help="Refresh seeded, non-archived moments and unchanged open plan dates.")
        parser.add_argument("--manifest", type=Path, help="Write a JSON account/partner roster without passwords or OTPs; use .local/demo-accounts.json.")

    def handle(self, *args, **options):
        if not options["dry_run"] and not options["confirm_local"]:
            raise CommandError("Pass --confirm-local to write demo data, or --dry-run to preview.")
        if not settings.DEBUG or not settings.OTP_MOCK_DELIVERY:
            raise CommandError("Demo seeding requires DEBUG=true and OTP mock delivery enabled.")
        try:
            if mock_code() is None:
                raise CommandError("A valid development OTP mock code is required for sign-in.")
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        if not 0 <= options["seed"] <= 2**31 - 1:
            raise CommandError("--seed must be between 0 and 2147483647.")
        manifest = Path(options["manifest"]) if options["manifest"] else None
        if manifest and manifest.exists():
            try:
                previous = json.loads(manifest.read_text(encoding="utf-8"))
                if previous.get("dataset") != DATASET or previous.get("seed") != options["seed"]:
                    raise ValueError()
            except (OSError, ValueError, AttributeError) as exc:
                raise CommandError("Refusing to overwrite a manifest path that does not belong to this demo dataset.") from exc
        dataset = DemoAccounts(seed=options["seed"], refresh_activity=options["refresh_activity"])
        try:
            if options["dry_run"]:
                existing = dataset.preflight()
                self.stdout.write(f"Dry run: {existing} existing demo accounts; would create {100-existing} users and {(100-existing)//2} couples.")
                self.stdout.write("Validated 100 synthetic people photos and 6 landscape photos. No database, media or manifest writes.")
                if options["refresh_activity"]:
                    self.stdout.write("Would also refresh eligible seeded moments and unchanged open plans.")
                return
            result = dataset.run()
        except DatabaseError as exc:
            raise CommandError("A database lock or constraint prevented demo seeding. The database transaction was rolled back. Retry after other writers finish.") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Demo accounts ready: created_users={result['created_users']} created_couples={result['created_couples']} "
            + " ".join(f"{key}={value}" for key, value in result["counts"].items())
        ))
        for user in result["accounts"][:2]:
            self.stdout.write(f"{user['name']}: {user['phone']} | partner: {user['partner_name']} ({user['partner_phone']})")
        self.stdout.write("Sign in through the app's normal OTP flow using the configured development mock code. Accounts have no usable password.")
        self.stdout.write("Matrix mappings are queued for the existing reconciler. Encrypted conversations and Spark answers must be created by clients.")
        if manifest:
            temporary = None
            try:
                manifest.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=manifest.parent, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(result, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                temporary.replace(manifest)
                self.stdout.write(f"Account roster: {manifest.resolve()}")
            except OSError as exc:
                raise CommandError("The demo dataset was committed, but its roster could not be written. Rerun with a writable --manifest path.") from exc
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
