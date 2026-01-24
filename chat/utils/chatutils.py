from chat.models import *
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from utils.helpers import get_base_url

def is_number_valid(number):
    return number.startswith("+") and len(number) > 11

def is_number_same(sender_number, receiver_number):
    return sender_number == receiver_number

def user_with_number_exists(number):
    return UserModel.objects.filter(phone_number=number).exists()

def has_permission(user_number,sender_number, receiver_number):
    return user_number == sender_number or user_number == receiver_number

def is_user_valid(user_number, sender_number):
    return user_number == sender_number

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
        message = MessageModel.objects.filter(id=message_id).first()
        return message
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
def serialize_message(message, scope):
    base_url = get_base_url(scope)

    return {
        "id": str(message.id),
        
        # Chat & connection info
        "chat_room": str(message.chat_room.id) if message.chat_room else None,
        "couple": message.couple.id if message.couple else None,
        "singles": message.singles.id if message.singles else None,
        
        # Sender/receiver
        "sender_id": message.sender.id,
        "receiver_id": message.receiver.id if message.receiver else None,

        # Message content
        "message_type": message.message_type,
        "text": message.text,
        "media": message.media.url if message.media else None,
        "media_url": message.media_url,
        "sticker_url": message.sticker_url,

        # Multiple medias (ManyToMany)
        "medias": [
            {
                "id": media.id,
                "media_url": f"{base_url}{media.file.url}",
                "uploaded_at": media.uploaded_at.isoformat()
            }
            for media in message.medias.all()
        ],


        # Reply
        "reply_to": {
            "id": message.reply_to.id,
            "text": message.reply_to.text,
            "sender_id": message.reply_to.sender.id,
            "message_type": message.reply_to.message_type,
        } if message.reply_to else None,

        # Message status
        "is_deleted": message.is_deleted,
        "is_read": message.is_read,
        "is_delivered": message.is_delivered,
        "is_edited": message.is_edited,
        "is_sent": message.is_sent,

        # Delete settings
        "deleted_message": message.deleted_message,
        "delete_option": message.delete_option,
        "delete_for": message.delete_for,
        "message_deletion_dict": message.message_deletion_dict,
        "reactions": message.reactions,

        # Reactions
        "reactions": message.reactions,

        # Time
        "timestamp": message.timestamp.isoformat(),
    }
