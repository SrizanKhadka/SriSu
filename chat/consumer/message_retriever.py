# chat/utils/chat_pagination.py

import asyncio
from asgiref.sync import sync_to_async
from chat.models import MessageModel
from utils.choices import DeleteOption
from django.db.models import Q
from chat.utils.chatutils import serialize_message

async def get_messages_before(scope,chat_room, user, page=None, limit=20):
    
    print("Fetching messages before cursor:", user.id)
    query_set = (
        MessageModel.objects
        .filter(chat_room=chat_room)
        .exclude(
            deletions__user_id=user.id,
            deletions__delete_option__in=[
                DeleteOption.DELETE_FOR_ME,
                DeleteOption.CONVERSATION_DELETED,
            ],
        )
        .order_by("-id").distinct()
    )

    if page:
        query_set = query_set.filter(id__lt=page)

    query_set = query_set[:limit]
    messages = await sync_to_async(list)(query_set)

    # Serialize concurrently
    results = await asyncio.gather(*(serialize_message(m,scope=scope) for m in messages))

    return {
        "messages": results,
        "has_more": len(results) == limit,
        "next_cursor": results[-1]["id"] if results else None
    }
