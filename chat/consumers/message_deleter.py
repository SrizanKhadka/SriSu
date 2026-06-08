
from chat.models import MessageModel,MessageDeletion
from channels.db import database_sync_to_async
from utils.choices import DeleteOption, ChatTypeChoices
from chat.utils.chatutils import *
from django.db import IntegrityError

async def handle_delete_for_me(message, user_id):

    user_id_str = str(user_id)

    try:
        await MessageDeletion.objects.acreate(
            messageModel=message,
            user_id=user_id,
            delete_option=DeleteOption.DELETE_FOR_ME,
        )
        deletion_created = True
    except IntegrityError:
        deletion_created = False

    if not message.delete_for:
        message.delete_for = {}

    user_entries = message.delete_for.setdefault(user_id_str, [])

    delete_entry = {
        "user_id": user_id_str,
        "delete_option": DeleteOption.DELETE_FOR_ME,
    }

    if delete_entry not in user_entries:
        user_entries.append(delete_entry)

    if deletion_created or delete_entry in user_entries:
        await save_message(message)

    return message

async def handle_delete_for_everyone(message, user_id):
    if not message:
        return None

    user_id_str = str(user_id)
    sender_id_str = str(message.sender_id)

    displayed_text = (
        "You deleted this message"
        if sender_id_str == user_id_str
        else "This message was deleted"
    )

    try:
        await MessageDeletion.objects.acreate(
            messageModel=message,
            user_id=user_id,
            delete_option=DeleteOption.DELETE_FOR_EVERYONE,
        )
        deletion_created = True
    except IntegrityError:
        deletion_created = False

    if not message.delete_for:
        message.delete_for = {}

    user_entries = message.delete_for.setdefault(user_id_str, [])

    delete_entry = {
        "user_id": user_id_str,
        "delete_option": DeleteOption.DELETE_FOR_EVERYONE,
        "delete_message": displayed_text,
    }

    # Upgrade DELETE_FOR_ME → DELETE_FOR_EVERYONE if needed
    upgraded = False
    for entry in user_entries:
        if entry.get("user_id") == user_id_str:
            if entry.get("delete_option") != DeleteOption.DELETE_FOR_EVERYONE:
                entry["delete_option"] = DeleteOption.DELETE_FOR_EVERYONE
                entry["delete_message"] = displayed_text
                upgraded = True
            break
    else:
        user_entries.append(delete_entry)
        upgraded = True

    if deletion_created or upgraded:
        await save_message(message)

    return message



async def handle_delete_message(data):
    message_id = data.get("id")
    user_id = data.get("user_id")
    delete_option = data.get("delete_option")

    if not message_id or not user_id or not delete_option:
        print("Missing required data for deletion")
        return None

    message = await get_message(message_id)

    if not message:
        print(f"Message {message_id} not found")
        return None

    if delete_option == DeleteOption.DELETE_FOR_ME:
        updated_message = await handle_delete_for_me(message, user_id)
    else:  # DELETE_FOR_EVERYONE
        updated_message = await handle_delete_for_everyone(message, user_id)

    print('MESSAGE DELETED SUCCESSFULLY.')

    return updated_message

#Note: This function can be used in future for bulk deletion of messages.
# async def handle_delete_message(data):
#     message_ids = data.get("id")  # assuming this is a list for bulk, or single int
#     user_id = data.get("user_id")
#     delete_option = data.get("delete_option")

#     # Handle single ID or list
#     if not isinstance(message_ids, list):
#         message_ids = [message_ids]

#     # Fetch all messages in bulk
#     messages = await get_messages(message_ids)  # assuming this returns list of MessageModel

#     tasks = []
#     for message in messages:
#         if delete_option == DeleteOption.DELETE_FOR_ME:
#             tasks.append(handle_delete_for_me(message, user_id))
#         else:
#             tasks.append(handle_delete_for_everyone(message, user_id))

#     # Run concurrently and get updated messages
#     updated_messages = await asyncio.gather(*tasks)

#     # Filter out any None if needed
#     updated_messages = [msg for msg in updated_messages if msg is not None]

#     # Notify callback if you still want (e.g., for logging)
#     print('MESSAGES DELETED SUCCESSFULLY.')

#     return updated_messages  # Return full updated messages


async def handle_delete_conversation(data):
    chat_type = data.get("chat_type")
    user_id = data.get("user_id")

    messages = []

    if chat_type == ChatTypeChoices.SINGLE:
        single_user = data.get("singles")
        if not single_user:
            return

        messages = await database_sync_to_async(list)(
            MessageModel.objects.filter(singles=single_user)
        )

    elif chat_type == ChatTypeChoices.COUPLE:
        couple = data.get("couple")
        if not couple:
            return

        messages = await database_sync_to_async(list)(
            MessageModel.objects.filter(couple=couple)
        )

    if messages:
        for message in messages:
            delete_entry = {
                "user_id": user_id,
                "delete_option": DeleteOption.CONVERSATION_DELETED,
                "deleted_message": None,
            }

            if not message.delete_for:
                message.delete_for = {}

            # Remove existing entry for this user
            message.delete_for = [
                entry for entry in message.delete_for if entry["user_id"] != user_id
            ]
            message.delete_for.append(delete_entry)

    # Bulk update messages
    await database_sync_to_async(MessageModel.objects.bulk_update)(
        messages, ["delete_for"]
    )

    # Notify all users in the chat about the conversation deletion
    # await self.channel_layer.group_send(
    #     self.room_group_name,
    #     {
    #         "type": "chat.conversation_deleted",
    #         "user_id": user_id,
    #         "message_ids": [msg.id for msg in messages],
    #     },
    # )
