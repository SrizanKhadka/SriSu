
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

    all_messages = MessageModel.objects.filter(chat_room=chat_room).order_by(
        "-timestamp"
    )

    filtered_messages = []

    for message in all_messages[offset : offset + page_size]:
        deleted_for = message.delete_for or []

        # Skip message if current user has any delete_for entry
        skip = False
        for entry in deleted_for:
            try:
                if entry.get("user_id") == user.id and entry.get(
                    "delete_option"
                ) in [
                    DeleteOption.DELETE_FOR_ME,
                    DeleteOption.CONVERSATION_DELETED,
                    DeleteOption.DELETE_FOR_EVERYONE,
                ]:
                    skip = True
                    break
            except Exception:
                continue

        if not skip:
            print(f'message = {message.text}')
            filtered_messages.append(message)

    return filtered_messages