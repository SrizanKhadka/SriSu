from chat.models import ChatRoom
from channels.db import database_sync_to_async

@database_sync_to_async
def set_user_typing(chat_room: ChatRoom, user_id: int, is_typing: bool):
    """
    Mark a user as typing or not typing in the chat room.
    Updates the `is_typing` JSONField.
    """
    user_id_str = str(user_id)
    typing_data = chat_room.is_typing or {}

    if is_typing:
        typing_data[user_id_str] = True
    else:
        typing_data.pop(user_id_str, None)  # remove if present

    chat_room.is_typing = typing_data
    chat_room.save()
    return typing_data


@database_sync_to_async
def get_typing_users(chat_room: ChatRoom):
    """
    Returns the list of user_ids currently typing in the chat room.
    """
    typing_data = chat_room.is_typing or {}
    return list(typing_data.keys())
