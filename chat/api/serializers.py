from rest_framework import serializers
from chat.models import *
from django.db.models import Q
from chat.utils.chatutils import *

class MediaModelSerializer(serializers.ModelSerializer):
    #In future make sure to validate the size of media to a certain size.
    class Meta:
        model = MediaModel
        fields = "__all__"


class ChatRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatRoom
        fields = "__all__"