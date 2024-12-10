from rest_framework import serializers
from chat.models import CoupleConnectionModel


class CoupleConnectionSerializer(serializers.ModelSerializer):

    class Meta:
        model = CoupleConnectionModel
        fields = "__all__"

    def validate(self, data):
    
        sender_number = data["sender_number"]
        receiver_number = data["receiver_number"]
        
        print(f'SENDER_NUMBER = {sender_number}')

        if not self.is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not self.is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")

        return data

    def is_number_valid(self, number):
        if not number.startswith("+") or len(number) < 10:
            return False
