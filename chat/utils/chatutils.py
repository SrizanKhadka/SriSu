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
def get_user(user_id):
        return UserModel.objects.filter(id=user_id).first()