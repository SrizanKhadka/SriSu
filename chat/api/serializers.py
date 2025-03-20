from rest_framework import serializers
from chat.models import *
from django.db.models import Q
from chat.utils.chatutils import *

class CoupleConnectionSerializer(serializers.ModelSerializer):

    class Meta:
        model = CoupleConnectionModel
        fields = "__all__"

    def validate(self, data):
        print("inside serializer validate")
        validated_data = super().validate(data)
        sender_number = validated_data["sender_number"]
        receiver_number = validated_data["receiver_number"]

        print(f"SENDER_NUMBER = {sender_number}")
        print("IS NUMBER VALID", is_number_valid(number=sender_number))

        if not is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")
        
        if is_number_same(sender_number, receiver_number):
            raise serializers.ValidationError("Sender and Receiver number can't be same.")

        if not user_with_number_exists(number=sender_number):
            raise serializers.ValidationError("User doesn't exists")
        elif not user_with_number_exists(number=receiver_number):
            raise serializers.ValidationError("Your Partner doesn't have an account.")

        return validated_data



class CouplePhotoAlbumSerializer(serializers.ModelSerializer):

    class Meta:
        model = PhotoAlbumModel
        fields = "__all__"


class CoupleModelSerializer(serializers.ModelSerializer):

    couple_photo_album = CouplePhotoAlbumSerializer(many=True, required=False)

    class Meta:
        model = CoupleModel
        fields = "__all__"

    def validate_couple_photo_album(self, photos):
        if photos and len(photos) > 10:
            raise serializers.ValidationError("You can only upload 10 photos.")
        return photos


class MediaModelSerializer(serializers.ModelSerializer):
    #In future make sure to validate the size of media to a certain size.
    class Meta:
        model = MediaModel
        fields = "__all__"


class SingleConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SingleConnectionModel
        fields = "__all__"
    
    def validate(self, data):
        print("inside serializer validate")
        validated_data = super().validate(data)
        sender_number = validated_data["sender_number"]
        receiver_number = validated_data["receiver_number"]

        print(f"SENDER_NUMBER = {sender_number}")
        print("IS NUMBER VALID", self.is_number_valid(number=sender_number))

        if not is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")

        if not user_with_number_exists(number=sender_number):
            raise serializers.ValidationError("User doesn't exists")
        elif not user_with_number_exists(number=receiver_number):
            raise serializers.ValidationError("Your Partner doesn't have an account.")
        
        return validated_data
        