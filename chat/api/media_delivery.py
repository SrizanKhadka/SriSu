"""Authorization-aware delivery for legacy chat files.

Ownerless historical files are never guessed.  They are readable only when a
single attached room can be derived and the requester can still access a
visible message in that room.
"""

from django.http import FileResponse
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from chat.models import MediaModel, MessageModel
from chat.selectors.access import authorized_rooms
from utils.choices import DeleteOption


def _visible_messages(queryset, user):
    return (
        queryset.filter(chat_room__in=authorized_rooms(user), is_deleted=False, tombstoned_at__isnull=True)
        .exclude(delete_option=DeleteOption.DELETE_FOR_ME)
        .exclude(visibility_records__user=user)
        .exclude(
            deletions__user=user,
            deletions__delete_option__in=[
                DeleteOption.DELETE_FOR_ME,
                DeleteOption.CONVERSATION_DELETED,
            ],
        )
    )


class LegacyChatMediaDelivery(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind, path):
        storage_name = f"{kind}/{path}"
        if kind == "chats/media":
            matches = list(MediaModel.objects.filter(file=storage_name)[:2])
            if len(matches) != 1:
                raise NotFound()
            media = matches[0]
            attached = media.messages.all()
            room_ids = list(attached.values_list("chat_room_id", flat=True).distinct()[:2])
            if room_ids:
                if len(room_ids) != 1 or not _visible_messages(attached, request.user).exists():
                    raise NotFound()
            elif media.owner_id != request.user.id or media.claimed_at is not None:
                # Unattached ownerless legacy rows are quarantined by denial.
                raise NotFound()
            file_field = media.file
        else:
            matches = list(MessageModel.objects.filter(media=storage_name)[:2])
            if len(matches) != 1:
                raise NotFound()
            message = matches[0]
            if not _visible_messages(MessageModel.objects.filter(pk=message.pk), request.user).exists():
                raise NotFound()
            file_field = message.media

        handle = file_field.open("rb")
        signature = handle.read(16)
        handle.seek(0)
        if signature.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
        elif signature.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
        else:
            handle.close()
            raise NotFound()
        response = FileResponse(handle, content_type=content_type)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
