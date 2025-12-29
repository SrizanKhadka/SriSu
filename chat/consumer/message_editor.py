
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q
from chat.models import MessageModel
from channels.db import database_sync_to_async
from chat.utils.chatutils import *

async def handle_edit_message(data):
    message_id = data.get("id")
    new_text = data.get("text")
    is_read = data.get("is_read", False)

    message = await get_message(message_id)
    if message:
        message.text = new_text
        message.is_delivered = True
        message.is_read = is_read
        message.is_edited = True
        await save_message(message)
        
        print(f"Message edited:  {message.text}")
        return message

async def handle_mark_messages_read(data, current_user):
    chat_room_id = data.get("chat_room_id")

    if not chat_room_id:
        return None

    try:
        chat_room = await sync_to_async(ChatRoom.objects.get)(id=chat_room_id)
    except ChatRoom.DoesNotExist:
        return None

    unread_messages = await sync_to_async(
        lambda: list(
            MessageModel.objects.filter(
                chat_room=chat_room,
                receiver=current_user,
                is_read=False,
                is_deleted=False
            )
        )
    )()

    if not unread_messages:
        return None

    # Mark as read
    for msg in unread_messages:
        msg.is_read = True

    await sync_to_async(MessageModel.objects.bulk_update)(
        unread_messages,
        ["is_read"]
    )

    return {
        "action": "messages_read",
        "chat_room_id": str(chat_room.id),
        "read_by": current_user.id,
        "message_ids": [msg.id for msg in unread_messages],
    }

        
async def handle_react_to_message(data):
    message_id = data.get("message_id")
    reaction = data.get("reaction")

    message = await get_message(message_id)
    if message:
        message.reaction = reaction
        await save_message(message)

        return message
    else:
        print("Message not found")
        return None