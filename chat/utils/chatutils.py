from chat.models import *
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async


def is_number_valid(number):
    return number.startswith("+") and len(number) > 11

def is_number_same(sender_number, receiver_number):
    return sender_number == receiver_number

def user_with_number_exists(number):
    return UserModel.objects.filter(phone_number=number).exists()

def has_permission(user_number,sender_number, receiver_number):
    return user_number == sender_number or user_number == receiver_number

@sync_to_async
def get_chat_room(chat_room_id):
    try:
        return ChatRoom.objects.filter(id=chat_room_id).first()
    except Exception as e:
        print(f"Error in get_chat_room: {e}")
        return None

@sync_to_async
def get_user(user_id):
    try:
        return UserModel.objects.filter(id=user_id).first()
    except Exception as e:
        print(f"Error in get_user: {e}")
        return None
@sync_to_async
def get_couple(couple_id):
    try:
        return CoupleModel.objects.filter(id=couple_id).first()
    except Exception as e:
        print(f"Error in get_couple: {e}")
        return None

@sync_to_async
def get_message(message_id):
    try:
        return MessageModel.objects.filter(id=message_id).first()
    except Exception as e:
        print(f"Error in get_message: {e}")
        return None

@sync_to_async #for fetching list of messages.
def get_messages(message_ids):
    try:
        return list(MessageModel.objects.filter(id__in=message_ids))
    except Exception as e:
        print(f"Error in get_messages: {e}")
        return []


@sync_to_async
def create_message(**kwargs):
    try:
        return MessageModel.objects.create(**kwargs)
    except Exception as e:
        print(f"Error in create_message: {e}")
        return None

@sync_to_async
def save_message(message):
    try:
        message.save()
    except Exception as e:
        print(f"Error in save_message: {e}")

@sync_to_async
def delete_message(message):
    try:
        message.delete()
    except Exception as e:
        print(f"Error in delete_message: {e}")

@sync_to_async
def serialize_message(message):
    return {
        "id": str(message.id),
        "chat_room_id": str(message.chat_room.id),
        "sender_id": str(message.sender.id),
        "text": message.text,
        "message_type": message.message_type,
        "medias": [media.file.url for media in message.medias.all()] if message.medias.exists() else [],  # ✅ Fix here
        "reply_to": str(message.reply_to.id) if message.reply_to else None,
        "reaction": message.reactions,
        "is_read": message.is_read,
        "is_delivered": message.is_delivered,
        "timestamp": message.timestamp.isoformat(),
    }

