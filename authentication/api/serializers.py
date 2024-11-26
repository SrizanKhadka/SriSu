from rest_framework import serializers
from authentication.models import UserModel

class SendOptSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserModel
        read_only_fields = [
            "created_date",
            "updated_date",
            "is_phone_verified",
            "is_profile_complete",
        ]
