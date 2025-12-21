# chat/utils/chat_pagination.py

import asyncio
from asgiref.sync import sync_to_async
from chat.models import MessageModel
from utils.choices import DeleteOption
from django.db.models import Q
from chat.utils.chatutils import serialize_message

async def get_messages_before(chat_room, user, page=None, limit=20):
    """
    Fetch messages before a specific message id (cursor-based).
    WhatsApp-like approach: newest messages first, supports infinite scroll.
    """
    query_set = (
        MessageModel.objects
        .filter(chat_room=chat_room)
        .exclude(
            Q(delete_for__user__contains=[{"user_id": user.id, "delete_option": DeleteOption.DELETE_FOR_ME}])
            | Q(delete_for__user__contains=[{"user_id": user.id, "delete_option": DeleteOption.CONVERSATION_DELETED}])
        )
        .order_by("-id")  # newest first
    )

    if page:
        query_set = query_set.filter(id__lt=page)

    query_set = query_set[:limit]
    messages = await sync_to_async(list)(query_set)

    # Serialize concurrently
    results = await asyncio.gather(*(serialize_message(m) for m in messages))

    return {
        "messages": results,
        "has_more": len(results) == limit,
        "next_cursor": results[-1]["id"] if results else None
    }
