
import asyncio
from asgiref.sync import sync_to_async
from chat.models import MessageModel
from utils.choices import DeleteOption
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from chat.utils.chatutils import serialize_message


class ChatMessagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


async def get_paginated_messages(chat_room, user, page, page_size):
    queryset = MessageModel.objects.filter(chat_room=chat_room).order_by("-timestamp")

    queryset = queryset.exclude(
        Q(delete_for__user__contains=[{"user_id": user.id, "delete_option": DeleteOption.DELETE_FOR_ME}])
        | Q(delete_for__user__contains=[{"user_id": user.id, "delete_option": DeleteOption.CONVERSATION_DELETED}])
    )

    paginator = ChatMessagePagination()
    paginator.page_size = page_size

    results_dict = await paginate_queryset(queryset, page, page_size, paginator)

    return results_dict


async def paginate_queryset(queryset, page, page_size, paginator):
    class DummyRequest:
        query_params = {"page": page, "page_size": page_size}

    # Run pagination in a sync thread because it hits DB
    page_obj = await sync_to_async(paginator.paginate_queryset, thread_sensitive=True)(
        queryset,
        DummyRequest(),
    )

    # Serialize concurrently
    results = await asyncio.gather(*(serialize_message(msg) for msg in page_obj))

    count = await sync_to_async(lambda: paginator.page.paginator.count, thread_sensitive=True)()
    next_page = await sync_to_async(
        lambda: paginator.page.next_page_number() if paginator.page.has_next() else None,
        thread_sensitive=True
    )()
    previous_page = await sync_to_async(
        lambda: paginator.page.previous_page_number() if paginator.page.has_previous() else None,
        thread_sensitive=True
    )()

    return {
        "count": count,
        "next_page": next_page,
        "previous_page": previous_page,
        "results": results,
    }