from django.db.models import Q
from rest_framework import serializers
from social.models import CoupleConnectionModel, CoupleModel, CoupleMomentModel, CoupleMomentPhotoModel, PhotoAlbumModel, SingleConnectionModel, UserPreferenceModel
from authentication.api.serializers import UserPhotoSerializer, UserInterestSerializer
from authentication.models import UserModel
from chat.utils.chatutils import is_number_valid, is_number_same, user_with_number_exists
from authentication.api.serializers import UserModelSerializer
from datetime import date
from utils.choices import SingleConnectionStatus


class CoupleConnectionSerializer(serializers.ModelSerializer):
    
    partner = serializers.SerializerMethodField()
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
    
    def get_partner(self, obj):
        """
        Returns the opposite user partner in the connection relative to the current request user.
        """
        request = self.context.get("request")
        print("Request in get_partner:", request)
        print("User in request:", getattr(request, "user", None))
        if not request or not hasattr(request, "user"):
            print("No request or user in context")
            return None

        current_user = request.user
        try:
            if current_user.phone_number == obj.sender_number:
                partner_user = UserModel.objects.get(phone_number=obj.receiver_number)
            else:
                partner_user = UserModel.objects.get(phone_number=obj.sender_number)

            return UserModelSerializer(partner_user, context=self.context).data
        except UserModel.DoesNotExist:
            print("Partner user not found")
            return None
        
class SingleConnectionSerializer(serializers.ModelSerializer):
    partner = serializers.SerializerMethodField()

    class Meta:
        model = SingleConnectionModel
        fields = "__all__"

    def validate(self, data):
        print("inside serializer validate")
        validated_data = super().validate(data)
        sender_number = validated_data["sender_number"]
        receiver_number = validated_data["receiver_number"]

        if not is_number_valid(number=sender_number):
            raise serializers.ValidationError("Sender_number is Invalid!")
        elif not is_number_valid(number=receiver_number):
            raise serializers.ValidationError("Receiver_number is Invalid!")

        if is_number_same(sender_number, receiver_number):
            raise serializers.ValidationError("Sender and Receiver number can't be the same.")

        if not user_with_number_exists(number=sender_number):
            raise serializers.ValidationError("User doesn't exist.")
        elif not user_with_number_exists(number=receiver_number):
            raise serializers.ValidationError("Your partner doesn't have an account.")

        return validated_data

    def get_partner(self, obj):
        """
        Returns the opposite user (partner) in the connection relative to the current request user.
        """
        request = self.context.get("request")
        if not request or not hasattr(request, "user"):
            return None

        current_user = request.user
        try:
            if current_user.phone_number == obj.sender_number:
                user = UserModel.objects.get(phone_number=obj.receiver_number)
            else:
                user = UserModel.objects.get(phone_number=obj.sender_number)

            return UserModelSerializer(user, context=self.context).data
        except UserModel.DoesNotExist:
            return None


class UserSuggestionSerializer(serializers.ModelSerializer):
    user_interests = UserInterestSerializer(many=True, read_only=True)
    user_photos = UserPhotoSerializer(many=True, read_only=True)
    has_active_connection = serializers.SerializerMethodField()
    age = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = [
            "id",
            "full_name",
            "phone_number",
            "username",
            "user_interests",
            "user_photos",
            "profile_photo",
            "city",
            "country",
            "age",
            "gender",
            "zodiac_sign",
            "mood",
            "bio",
            "has_active_connection",
        ]
        read_only_fields = fields

    def get_age(self, obj):
        if not obj.dob:
            return None

        today = date.today()
        return today.year - obj.dob.year - (
            (today.month, today.day) < (obj.dob.month, obj.dob.day)
        )

    def get_has_active_connection(self, obj):
        annotated_value = getattr(obj, "has_active_connection", None)
        if annotated_value is not None:
            return annotated_value

        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
            return False

        current_user_number = request.user.phone_number
        if not current_user_number or not obj.phone_number:
            return False

        return SingleConnectionModel.objects.filter(
            Q(
                sender_number=current_user_number,
                receiver_number=obj.phone_number,
            )
            | Q(
                receiver_number=current_user_number,
                sender_number=obj.phone_number,
            )
        ).exclude(
            connection_status__in=[
                SingleConnectionStatus.NOTHING,
                SingleConnectionStatus.REJECTED,
            ]
        ).exists()

class UserPreferenceSerializer(serializers.ModelSerializer):
    
    user = serializers.PrimaryKeyRelatedField(
            queryset=UserModel.objects.all(),
            write_only=True
        )    
    class Meta:
        model = UserPreferenceModel
        fields = "__all__"
class CouplePhotoAlbumSerializer(serializers.ModelSerializer):

    class Meta:
        model = PhotoAlbumModel
        fields = "__all__"

class CoupleModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoupleModel
        fields = "__all__"   

class CoupleMomentPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoupleMomentPhotoModel
        fields = ["id", "image", "order", "uploaded_at"]
        read_only_fields = ["id", "uploaded_at"]


class CoupleMomentSerializer(serializers.ModelSerializer):
    photos = CoupleMomentPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = CoupleMomentModel
        fields = [
            "id",
            "couple",
            "created_by",
            "title",
            "caption",
            "moment_date",
            "mood",
            "location_name",
            "visibility",
            "tags",
            "partner_memory",
            "photos",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "created_by"]
    
    def validate(self, data):
        request = self.context.get("request")
        couple = data.get("couple") or getattr(self.instance, "couple", None)
        
        if couple and request:
            user = request.user
            if user not in [couple.male_partner, couple.female_partner]:
                raise serializers.ValidationError("You are not allowed to create or update moment for this couple.")
        
        return data
                
    


