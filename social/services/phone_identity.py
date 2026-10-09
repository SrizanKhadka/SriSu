"""Small phone-identity predicates shared by relationship API validation."""

from authentication.models import UserModel


def is_number_valid(number):
    return number.startswith("+") and len(number) > 11


def is_number_same(sender_number, receiver_number):
    return sender_number == receiver_number


def user_with_number_exists(number):
    return UserModel.objects.filter(phone_number=number).exists()


def has_permission(user_number, sender_number, receiver_number):
    return user_number in {sender_number, receiver_number}


def is_user_valid(user_number, sender_number):
    return user_number == sender_number
