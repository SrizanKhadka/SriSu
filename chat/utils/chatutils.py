from chat.models import *

def is_number_valid(number):
    return number.startswith("+") and len(number) > 11

def user_with_number_exists(number):
    return UserModel.objects.filter(phone_number=number).exists()