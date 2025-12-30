from chat.models import ChatRoom
from channels.db import database_sync_to_async
from django.db import transaction
from chat.models import MessageModel

@database_sync_to_async
def set_user_typing(chat_room: ChatRoom, user_id: int, is_typing: bool):
    user_id_str = str(user_id)

    # Start an atomic database transaction
    # select_for_update() locks this ChatRoom row until the transaction ends
    with transaction.atomic():

        # Re-fetch and LOCK the chat room row to prevent concurrent overwrites
        chat_room = ChatRoom.objects.select_for_update().get(id=chat_room.id)
        typing_data = chat_room.is_typing or {}

        if is_typing:
            typing_data[user_id_str] = True
        else:
            typing_data.pop(user_id_str, None)

        chat_room.is_typing = typing_data
        chat_room.save(update_fields=["is_typing"])

    return typing_data
