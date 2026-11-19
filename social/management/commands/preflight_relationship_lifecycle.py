from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Read-only preflight for relationship/chat lifecycle migrations."

    def handle(self, *args, **options):
        quote = connection.ops.quote_name
        membership = quote("social_couplemembershipmodel")
        couple = quote("social_couplemodel")
        room = quote("chat_chatroom")
        message = quote("chat_messagemodel")
        deletion = quote("chat_messagedeletion")
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT membership.couple_id
                FROM {membership} membership
                JOIN {couple} couple ON couple.id = membership.couple_id
                WHERE couple.couple_connection_id IS NULL
                ORDER BY membership.couple_id
                LIMIT 50
                """
            )
            orphan_couple_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                f"""
                SELECT couple_id, COUNT(*)
                FROM {room}
                WHERE couple_id IS NOT NULL
                GROUP BY couple_id
                HAVING COUNT(*) > 1
                ORDER BY couple_id
                LIMIT 50
                """
            )
            duplicate_rooms = cursor.fetchall()
            cursor.execute(
                f"""
                SELECT message.id
                FROM {message} message
                LEFT JOIN {deletion} deletion
                  ON deletion.message_id = message.id
                 AND deletion.delete_option = 'DELETE_FOR_ME'
                WHERE message.delete_option = 'DELETE_FOR_ME'
                  AND deletion.id IS NULL
                ORDER BY message.id
                LIMIT 50
                """
            )
            ambiguous_private_deletions = [row[0] for row in cursor.fetchall()]

        problems = []
        if orphan_couple_ids:
            problems.append(
                "unlinked membership couple ids="
                + ",".join(str(item) for item in orphan_couple_ids)
            )
        if duplicate_rooms:
            problems.append(
                "duplicate room couple_id:count="
                + ",".join(f"{couple_id}:{count}" for couple_id, count in duplicate_rooms)
            )
        if ambiguous_private_deletions:
            problems.append(
                "legacy DELETE_FOR_ME message ids without actor="
                + ",".join(str(item) for item in ambiguous_private_deletions)
            )
        if problems:
            raise CommandError(
                "; ".join(problems)
                + ". Resolve each identity/history explicitly; this command never mutates data."
            )
        self.stdout.write("relationship_lifecycle_preflight=ok")
