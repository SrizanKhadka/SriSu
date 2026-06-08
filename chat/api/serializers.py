from rest_framework import serializers

from chat.models import ChatRoom, MediaModel

class MediaModelSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaModel
        fields = ["id", "file", "file_url", "uploaded_at"]
        read_only_fields = ["id", "uploaded_at", "file_url"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not obj.file:
            return None

        try:
            url = obj.file.url
        except Exception:
            return None

        if request:
            return request.build_absolute_uri(url)
        return url


class ChatRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatRoom
        fields = [
            "id",
            "chat_type",
            "user_one",
            "user_two",
            "couple",
            "singles",
            "last_message",
            "unread_count",
            "is_typing",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields