"""Explicitly purge only the retired Django chat media namespaces."""

import posixpath
from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


LEGACY_PREFIXES = (
    "chats/media",
    "chats_media",
    "messages/media",
    "chat_encrypted",
)
CONFIRMATION = "DESTROY_LEGACY_CHAT_MEDIA"


def _child_name(prefix, child):
    child = child.replace("\\", "/")
    if not child or "/" in child or child in {".", ".."}:
        raise CommandError("Storage returned an invalid child path.")
    root = PurePosixPath(prefix)
    candidate = PurePosixPath(posixpath.join(prefix, child))
    if candidate.is_absolute() or candidate == root.parent or root not in candidate.parents:
        raise CommandError("Storage returned a path outside the legacy chat allowlist.")
    return candidate.as_posix()


def iter_legacy_names(storage):
    """Yield files recursively, never walking outside the fixed allowlist."""
    for prefix in LEGACY_PREFIXES:
        pending = [prefix]
        while pending:
            directory = pending.pop()
            try:
                directories, files = storage.listdir(directory)
            except FileNotFoundError:
                continue
            for filename in sorted(files):
                yield _child_name(directory, filename)
            for child in sorted(directories, reverse=True):
                pending.append(_child_name(directory, child))


class Command(BaseCommand):
    help = (
        "List retired Django chat media by default. Deletion requires both "
        "--execute and the exact destructive confirmation phrase."
    )

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        execute = options["execute"]
        confirmation = options["confirm"]
        if execute and confirmation != CONFIRMATION:
            raise CommandError(
                f"Execution requires --confirm {CONFIRMATION}."
            )
        if confirmation and not execute:
            raise CommandError("--confirm is valid only together with --execute.")

        names = list(iter_legacy_names(default_storage))
        mode = "DELETE" if execute else "DRY-RUN"
        for name in names:
            self.stdout.write(f"{mode} {name}")
        if execute:
            for name in names:
                default_storage.delete(name)
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode.lower()} complete: {len(names)} legacy chat object(s)."
            )
        )
