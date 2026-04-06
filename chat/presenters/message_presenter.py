from __future__ import annotations

from asgiref.sync import sync_to_async

from chat.models import MessageModel
from utils.helpers import get_base_url


def _absolute_url(base_url: str, relative_url: str | None) -> str | None:
    if not relative_url:
        return None
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    return f"{base_url}{relative_url}"


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
        "media_url": _absolute_url(base_url, message.media.url if message.media else message.media_url),
        "sticker_url": message.sticker_url,
        "medias": [
            {
                "id": media.id,
                "media_url": _absolute_url(base_url, media.file.url),
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
        if message.reply_to
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


@sync_to_async
def serialize_message_for_socket(message: MessageModel, scope) -> dict:
    return serialize_message_for_socket_sync(message, scope)