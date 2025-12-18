
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

async def handle_mark_messages_read(data):
    receiver_id = data.get("receiver_id")
    print(f"Receiver ID: {receiver_id}")

    if not receiver_id: #check if user_id is equal to receiver_id
        return None

    # Get receiver as UserModel instance
    try:
        receiver = await sync_to_async(UserModel.objects.get)(id=receiver_id)
    except UserModel.DoesNotExist:
        print("Receiver not found")
        return None

    # Get unread messages for this receiver
    unread_messages = await sync_to_async(
        lambda: list(MessageModel.objects.filter(receiver=receiver, is_read=True).all())
    )()
    
    # print("UN_READ MESSAGES: ", serialize_message(unread_messages))

    if unread_messages:
        for msg in unread_messages:
            msg.is_read = True
        
        print("MESSAGES ARE READ: ")

        await sync_to_async(MessageModel.objects.bulk_update)(unread_messages, ["is_read"])

        # Notify participants
        return unread_messages
    else:
        print("No unread messages found")
        return None
        
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