
from chat.models import MessageModel
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

async def handle_mark_messages_read(data):
    chat_room_id = data.get("chat_room")
    current_user = data.get("user_id")

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
        print('No unread messages found')
        return None

    # Mark as read
    for msg in unread_messages:
        msg.is_read = True

    await sync_to_async(MessageModel.objects.bulk_update)(
        unread_messages,
        ["is_read"]
    )
    
    print('All messages marked as read')

    return {
        "action": "messages_read",
        "chat_room_id": str(chat_room.id),
        "read_by": current_user,
        "message_ids": [msg.id for msg in unread_messages],
    }

async def handle_mark_messages_delivered(data):
    chat_room_id = data.get("chat_room")
    current_user = data.get("user_id")

    if not chat_room_id:
        return None

    try:
        chat_room = await sync_to_async(ChatRoom.objects.get)(id=chat_room_id)
    except ChatRoom.DoesNotExist:
        return None

    undelivered_message = await sync_to_async(
        lambda: list(
            MessageModel.objects.filter(
                chat_room=chat_room,
                receiver=current_user,
                is_delivered=False,
                is_deleted=False
            )
        )
    )()

    if not undelivered_message:
        print('No undelivered messages found')
        return None

    # Mark as read
    for msg in undelivered_message:
        msg.is_delivered = True

    await sync_to_async(MessageModel.objects.bulk_update)(
        undelivered_message,
        ["is_delivered"]
    )

    print('All messages marked as delivered')
    return {
        "action": "messages_delivered",
        "chat_room_id": str(chat_room.id),
        "delivered_to": current_user,
        "message_ids": [msg.id for msg in undelivered_message],
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