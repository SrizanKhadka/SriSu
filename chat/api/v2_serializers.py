from rest_framework import serializers


class MessagePageQuery(serializers.Serializer):
    before_sequence = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(default=20, min_value=1, max_value=50)


class ChangePageQuery(serializers.Serializer):
    after_sequence = serializers.IntegerField(default=0, min_value=0)
    limit = serializers.IntegerField(default=50, min_value=1, max_value=100)


class SendEncryptedMessageSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    content_kind = serializers.ChoiceField(choices=("text",))
    envelope = serializers.JSONField()
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    attachment_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        max_length=10,
    )


class MessageOperationSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    action = serializers.ChoiceField(
        choices=("edit", "delete_for_me", "delete_for_everyone", "set_reaction")
    )
    message_id = serializers.IntegerField(min_value=1)
    expected_revision = serializers.IntegerField(required=False, min_value=1)
    envelope = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        action = attrs["action"]
        if action in ("edit", "delete_for_everyone") and "expected_revision" not in attrs:
            raise serializers.ValidationError({"expected_revision": "This field is required."})
        if action == "edit" and attrs.get("envelope") is None:
            raise serializers.ValidationError({"envelope": "This field is required."})
        if action == "set_reaction" and "envelope" not in attrs:
            raise serializers.ValidationError(
                {"envelope": "Provide an envelope, or null to remove the reaction."}
            )
        return attrs


class ReceiptSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    delivered_through = serializers.IntegerField(min_value=0)
    read_through = serializers.IntegerField(min_value=0)


class EncryptedAttachmentUploadSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    ciphertext_sha256 = serializers.RegexField(r"^[0-9a-f]{64}$")
    ciphertext = serializers.FileField()
