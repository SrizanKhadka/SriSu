from rest_framework import serializers
from chat.models import *
from django.db.models import Q

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
        print("IS NUMBER VALID", self.is_number_valid(number=sender_number))

        if not self.is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not self.is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")

        if not self.user_with_number_exists(number=sender_number):
            raise serializers.ValidationError("User doesn't exists")
        elif not self.user_with_number_exists(number=receiver_number):
            raise serializers.ValidationError("Your Partner doesn't have an account.")

        is_sender_engaged = self.is_already_engaged(number=sender_number)
        is_receiver_engaged = self.is_already_engaged(number=receiver_number)

        if is_sender_engaged:
            raise serializers.ValidationError({"message": "You are already engaged!"})

        if is_receiver_engaged:
            raise serializers.ValidationError(
                {"message": "Requested Person is already engaged!"}
            )

        return validated_data

    def is_number_valid(self, number):
        return number.startswith("+") and len(number) > 11

    def user_with_number_exists(self, number):
        return UserModel.objects.filter(phone_number=number).exists()

    def is_already_engaged(self, number):
        return CoupleConnectionModel.objects.filter(
            Q(sender_number=number, connection_status=CoupleConnectionStatus.ACCEPTED)
            | Q(
                receiver_number=number,
                connection_status=CoupleConnectionStatus.ACCEPTED,
            )
        ).exists()


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