
from asgiref.sync import sync_to_async
from django.db.models import Q
from chat.models import MessageModel
from utils.choices import DeleteOption

async def handle_fetch_messages(user, chat_room, data):
    page = int(data.get("page", 1))
    page_size = int(data.get("page_size", 20))
    messages = await get_paginated_messages(
        chat_room, user, page, page_size
    )
    
    # on_message_fetched(messages)
    return messages

@sync_to_async
def get_paginated_messages(chat_room, user, page, page_size):
    offset = (page - 1) * page_size

    queryset = (
        MessageModel.objects
        .filter(chat_room=chat_room)
        .order_by("-timestamp")[offset: offset + page_size]
    )

    filtered_messages = []

    for message in queryset:
        skip = False

        if message.delete_for:
            for entry in message.delete_for.get("user", []):
                if (
                    str(entry.get("user_id")) == str(user)
                    and entry.get("delete_option") in [
                        DeleteOption.DELETE_FOR_ME,
                        DeleteOption.CONVERSATION_DELETED,
                    ]
                ):
                    skip = True
                    break

        if not skip:
            filtered_messages.append(message)

    return filtered_messages
