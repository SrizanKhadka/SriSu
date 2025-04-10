
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q
from chat.models import MessageModel
from channels.db import database_sync_to_async
from chat.utils.chatutils import *

async def handle_edit_message(data, on_message_edited):
        message_id = data.get("message_id")
        new_text = data.get("new_text")
        is_read = data.get("is_read", False)

        message = await get_message(message_id)
        if message:
            message.text = new_text
            message.is_delivered = True
            message.is_read = is_read
            message.is_edited = True
            await save_message(message)
            
            on_message_edited(message)

async def handle_mark_messages_read(data, on_messages_read):
    receiver_id = data.get("receiver_id")

    if not receiver_id:
        return

    # Fetch all unread messages sent to this receiver
    unread_messages = await database_sync_to_async(
        lambda: list(
            MessageModel.objects.filter(receiver=receiver_id, is_read=False)
        )
    )()

    if unread_messages:
        # Bulk update messages as read
        for message in unread_messages:
            message.is_read = True

        await database_sync_to_async(MessageModel.objects.bulk_update)(
            unread_messages, ["is_read"]
        )

        # Notify all participants that messages are now read
        on_messages_read(unread_messages)
        
async def handle_react_to_message(data, on_message_reacted):
    message_id = data.get("message_id")
    reaction = data.get("reaction")

    message = await get_message(message_id)
    if message:
        message.reaction = reaction
        await save_message(message)

        on_message_reacted(message)