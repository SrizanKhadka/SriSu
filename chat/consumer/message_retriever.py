
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

    all_messages = MessageModel.objects.filter(chat_room=chat_room).order_by("-timestamp")

    filtered_messages = []
    deleted_for = []

    for message in all_messages[offset : offset + page_size]:
        skip = False
        if message.delete_for:
            deleted_for = message.delete_for[str(user)]
            if isinstance(deleted_for, list):  # just to be safe
                print('INSTANCE OF LIST')
                for entry in deleted_for:
                    print('ENTRY', entry)
                    try:
                        print('INSIDE OF TRY')
                        if str(entry.get("user_id")) == str(user) and entry.get("delete_option") in [
                            DeleteOption.DELETE_FOR_ME,
                            DeleteOption.CONVERSATION_DELETED,
                            # DeleteOption.DELETE_FOR_EVERYONE,  # optional
                        ]:
                            print('SKIPPING')
                            skip = True
                            break
                    except Exception as e:
                        print(f"Error parsing delete_for entry: {e}")
                        continue
                    
        if not skip:
            filtered_messages.append(message)

    return filtered_messages