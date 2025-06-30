
# from chat.models import MessageModel
# from channels.db import database_sync_to_async
# from utils.choices import DeleteOption, ChatTypeChoices
# import asyncio
# from chat.utils.chatutils import *

# async def handle_delete_for_me(data):
#     message_id = data.get("message_id")
#     user_id = str(data.get("user_id")) 
#     message = await get_message(message_id)

#     if message and not message.delete_for:
#         delete_entry = {
#             "user_id": user_id,
#             "delete_option": DeleteOption.DELETE_FOR_ME,
#             "deleted_message": None,
#         }

#         if not message.delete_for:
#             message.delete_for = {}

#         # Initialize list for this user if it doesn't exist
#         if user_id not in message.delete_for:
#             message.delete_for[user_id] = []

#         # Avoid adding duplicate entry
#         if delete_entry not in message.delete_for[user_id]:
#             message.delete_for[user_id].append(delete_entry)

#         await save_message(message)
#     else:
#         await save_message(message)




# async def handle_delete_for_everyone(data):
#     message_id = data.get("message_id")
#     user_id = str(data.get("user_id"))

#     message = await get_message(message_id)
    
#     if message:
#         # print(f"Message ID: {message_id}, User ID: {user_id}")
#         # print(f"Message: {message.text}, Sender ID: {message.sender_id}")
#         sender_id = message.sender_idti
#         delete_message = (
#             "You deleted this message"
#             if sender_id == user_id
#             else "This message was deleted"
#         )

#         delete_entry = {
#             "user_id": user_id,
#             "option": DeleteOption.DELETE_FOR_EVERYONE,
#             "delete_message": delete_message,
#         }

#         if not message.delete_for:
#             message.delete_for = {}

#         # Initialize list for this user if it doesn't exist
#         if user_id not in message.delete_for:
#             message.delete_for[user_id] = []

#         # Avoid adding duplicate entry
#         if delete_entry not in message.delete_for[user_id]:
#             message.delete_for[user_id].append(delete_entry)

#         await save_message(message)
#     else:
#         await save_message(message)

# async def handle_delete_message(data,on_message_deleted):
#     message_ids = data.get("message_id")
#     user_id = data.get("user_id")
#     delete_option = data.get("delete_option")

#     messages = await get_messages(message_ids)  # Fetch messages in bulk
#     tasks = []  # Store async tasks for execution

#     for message in messages:
#         delete_data = {
#             "message_id": message.id,
#             "user_id": user_id,
#         }

#         if delete_option == DeleteOption.DELETE_FOR_ME:
#             tasks.append(handle_delete_for_me(delete_data))
#         else:
#             tasks.append(handle_delete_for_everyone(delete_data))

#         # Run all delete tasks concurrently
#     await asyncio.gather(*tasks)

#         # Notify all users in the chat about bulk deletion
#     on_message_deleted(message_ids)

# async def handle_delete_conversation(data):
#     chat_type = data.get("chat_type")
#     user_id = data.get("user_id")

#     messages = []

#     if chat_type == ChatTypeChoices.SINGLE:
#         single_user = data.get("singles")
#         if not single_user:
#             return

#         messages = await database_sync_to_async(list)(
#             MessageModel.objects.filter(singles=single_user)
#         )

#     elif chat_type == ChatTypeChoices.COUPLE:
#         couple = data.get("couple")
#         if not couple:
#             return

#         messages = await database_sync_to_async(list)(
#             MessageModel.objects.filter(couple=couple)
#         )

#     if messages:
#         for message in messages:
#             delete_entry = {
#                 "user_id": user_id,
#                 "delete_option": DeleteOption.CONVERSATION_DELETED,
#                 "deleted_message": None,
#             }

#             if not message.delete_for:
#                 message.delete_for = {}

#             # Remove existing entry for this user
#             message.delete_for = [
#                 entry for entry in message.delete_for if entry["user_id"] != user_id
#             ]
#             message.delete_for.append(delete_entry)

#     # Bulk update messages
#     await database_sync_to_async(MessageModel.objects.bulk_update)(
#         messages, ["delete_for"]
#     )

#     # Notify all users in the chat about the conversation deletion
#     # await self.channel_layer.group_send(
#     #     self.room_group_name,
#     #     {
#     #         "type": "chat.conversation_deleted",
#     #         "user_id": user_id,
#     #         "message_ids": [msg.id for msg in messages],
#     #     },
#     # )
