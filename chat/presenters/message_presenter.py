from __future__ import annotations

from channels.db import database_sync_to_async

from chat.api.media_urls import guarded_media_url
from chat.models import MessageDeletion, MessageModel
from utils.choices import DeleteOption
from utils.helpers import get_base_url


def _absolute_url(base_url: str, relative_url: str | None) -> str | None:
    if not relative_url:
        return None
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    return f"{base_url}{relative_url}"


def _reply_visible_to_scope(message: MessageModel, scope) -> bool:
    reply = message.reply_to
    if (
        reply is None
        or reply.is_deleted
        or reply.tombstoned_at is not None
        or reply.delete_option == DeleteOption.DELETE_FOR_ME
    ):
        return False
    viewer = scope.get("user") if isinstance(scope, dict) else None
    if not getattr(viewer, "is_authenticated", False):
        return False
    prefetched = getattr(reply, "_viewer_deletions", None)
    if prefetched is not None:
        return not prefetched
    return not MessageDeletion.objects.filter(
        message=reply,
        user=viewer,
        delete_option__in=[
            DeleteOption.DELETE_FOR_ME,
            DeleteOption.CONVERSATION_DELETED,
        ],
    ).exists()


def serialize_message_for_socket_sync(message: MessageModel, scope) -> dict:
    """
    Socket-friendly message serializer.

    This presenter shapes message payloads for websocket consumers and keeps a stable
    contract for frontend clients. It is intentionally separate from DRF serializers.
    """
    base_url = get_base_url(scope)

    return {
        "id": message.id,
        "chat_room_id": str(message.chat_room_id),
        "sender_id": message.sender_id,
        "receiver_id": message.receiver_id,
        "message_type": message.message_type,
        "text": message.text,
        "profile_action": message.profile_action if not message.is_deleted else None,
        "media_url": (
            guarded_media_url(message.media, base_url=base_url)
            if message.media and not message.is_deleted
            else None if message.couple_id else _absolute_url(base_url, message.media_url)
        ),
        "sticker_url": None if message.couple_id or message.is_deleted else message.sticker_url,
        "medias": [] if message.is_deleted else [
            {
                "id": media.id,
                "media_url": guarded_media_url(media.file, base_url=base_url),
                "uploaded_at": media.uploaded_at.isoformat(),
            }
            for media in message.medias.all()
        ],
        "reply_to": {
            "id": message.reply_to.id,
            "text": message.reply_to.text,
            "sender_id": message.reply_to.sender_id,
            "message_type": message.reply_to.message_type,
            "message_owner_name": getattr(message.reply_to.sender, "full_name", None),
        }
        if _reply_visible_to_scope(message, scope)
        else None,
        "is_deleted": message.is_deleted,
        "is_read": message.is_read,
        "is_delivered": message.is_delivered,
        "is_edited": message.is_edited,
        "is_sent": message.is_sent,
        "deleted_message": message.deleted_message,
        "delete_option": message.delete_option,
        # Transitional compatibility field while older clients still consume JSON reactions.
        "reactions": getattr(message, "reactions", None),
        "timestamp": message.timestamp.isoformat(),
    }


@database_sync_to_async
def serialize_message_for_socket(message: MessageModel, scope) -> dict:
    return serialize_message_for_socket_sync(message, scope)
