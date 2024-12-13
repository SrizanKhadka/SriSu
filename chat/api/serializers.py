from rest_framework import serializers
from chat.models import *


class CoupleConnectionSerializer(serializers.ModelSerializer):

    class Meta:
        model = CoupleConnectionModel
        fields = "__all__"

    def validate(self, data):
        print("INSIDE VALIDATE COUPLE CONNECTION API")
        # validated_data = super().validate(data)
        sender_number = data["sender_number"]
        receiver_number = data["receiver_number"]

        print(f"SENDER_NUMBER = {sender_number}")

        if not self.is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not self.is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")

        return validated_data

    def is_number_valid(self, number):
        return not number.startswith("+") or len(number) < 10


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
