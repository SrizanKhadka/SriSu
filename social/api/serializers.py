from rest_framework import serializers
from social.models import CoupleConnectionModel, CoupleModel, PhotoAlbumModel, SingleConnectionModel, UserPreferenceModel
from authentication.api.serializers import UserPhotoSerializer, UserInterestSerializer
from authentication.models import UserModel
from chat.utils.chatutils import is_number_valid, is_number_same, user_with_number_exists
from authentication.api.serializers import UserModelSerializer

class CoupleConnectionSerializer(serializers.ModelSerializer):

    class Meta:
        model = CoupleConnectionModel
        fields = "__all__"

    def validate(self, data):
        validated_data = super().validate(data)
        sender_number = validated_data["sender_number"]
        receiver_number = validated_data["receiver_number"]


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

class SingleConnectionSerializer(serializers.ModelSerializer):
    
    receiver = serializers.SerializerMethodField()
    class Meta:
        model = SingleConnectionModel
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
    
    def get_receiver(self, obj):
        try:
            user = UserModel.objects.get(phone_number=obj.receiver_number)
            return UserModelSerializer(user).data
        except UserModel.DoesNotExist:
            return None

class UserSuggestionSerializer(serializers.ModelSerializer):
    user_interests = UserInterestSerializer(many=True, read_only=True)
    user_photos = UserPhotoSerializer(many=True, read_only=True)
    crushed = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserModel
        fields = [
            "id",
            "phone_number",
            "full_name",
            "username",
            "user_interests",
            "user_photos",
            "profile_photo",
            "city",
            "country",
            "dob",
            "gender",
            "zodiac_sign",
            "mood",
            "bio",
            "crushed"
        ]


class UserPreferenceSerializer(serializers.ModelSerializer):
    
    user = serializers.PrimaryKeyRelatedField(
            queryset=UserModel.objects.all(),
            write_only=True
        )    
    class Meta:
        model = UserPreferenceModel
        fields = "__all__"
