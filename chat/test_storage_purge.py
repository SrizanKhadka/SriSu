from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from chat.management.commands.purge_legacy_chat_storage import CONFIRMATION


class LegacyChatStoragePurgeTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="srisu-chat-purge-test-")
        self.addCleanup(self.temporary.cleanup)
        self.storage = FileSystemStorage(location=self.temporary.name)
        self.legacy_names = (
            "chats/media/one.jpg",
            "chats_media/nested/two.jpg",
            "messages/media/three.bin",
            "chat_encrypted/nested/four.bin",
        )
        self.unrelated_names = (
            "couples/moments/keep.jpg",
            "couples/profile_private/keep.jpg",
            "media/keep.jpg",
        )
        for name in (*self.legacy_names, *self.unrelated_names):
            self.storage.save(name, ContentFile(b"synthetic"))

    def command(self, *args, **kwargs):
        output = StringIO()
        with patch(
            "chat.management.commands.purge_legacy_chat_storage.default_storage",
            self.storage,
        ):
            call_command("purge_legacy_chat_storage", *args, stdout=output, **kwargs)
        return output.getvalue()

    def test_default_is_dry_run_and_lists_only_allowlisted_objects(self):
        output = self.command()
        for name in self.legacy_names:
            self.assertIn(f"DRY-RUN {name}", output)
            self.assertTrue(self.storage.exists(name))
        for name in self.unrelated_names:
            self.assertNotIn(name, output)
            self.assertTrue(self.storage.exists(name))

    def test_execute_requires_exact_confirmation(self):
        for confirmation in ("", "yes", "destroy"):
            with self.subTest(confirmation=confirmation), self.assertRaises(CommandError):
                self.command(execute=True, confirm=confirmation)
        for name in (*self.legacy_names, *self.unrelated_names):
            self.assertTrue(self.storage.exists(name))

    def test_confirmed_execute_never_touches_unrelated_prefixes(self):
        output = self.command(execute=True, confirm=CONFIRMATION)
        self.assertIn("delete complete: 4 legacy chat object(s).", output)
        for name in self.legacy_names:
            self.assertFalse(self.storage.exists(name))
        for name in self.unrelated_names:
            self.assertTrue(self.storage.exists(name))
