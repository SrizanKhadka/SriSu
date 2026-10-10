"""Bounded wire protocol. The server validates framing, never decrypts payloads."""
import base64
import binascii

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Unknown fields or invalid object."]})
        return super().to_internal_value(data)


class PublicBytes(serializers.CharField):
    def __init__(self, size, **kwargs):
        self.size = size
        super().__init__(max_length=((size + 2) // 3) * 4, trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise serializers.ValidationError("Invalid base64.") from None
        if len(decoded) != self.size or base64.b64encode(decoded).decode("ascii") != value:
            raise serializers.ValidationError("Invalid key size or encoding.")
        return value


class ECKey(StrictSerializer):
    id = serializers.IntegerField(min_value=1, max_value=2147483647)
    public_key = PublicBytes(33)


class SignedKey(ECKey):
    signature = PublicBytes(64)


class KyberKey(SignedKey):
    public_key = PublicBytes(1569)


class Keys(StrictSerializer):
    signed_prekey = SignedKey()
    ec_prekeys = ECKey(many=True, allow_empty=True, max_length=100)
    kyber_prekeys = KyberKey(many=True, allow_empty=True, max_length=30)

    def validate(self, data):
        for field in ("ec_prekeys", "kyber_prekeys"):
            ids = [key["id"] for key in data[field]]
            if len(set(ids)) != len(ids):
                raise serializers.ValidationError("Duplicate prekey identifiers.")
        return data


class Registration(Keys):
    device_id = serializers.UUIDField()
    replace_device_id = serializers.UUIDField(allow_null=True, default=None)
    registration_id = serializers.IntegerField(min_value=1, max_value=16383)
    identity_key = PublicBytes(33)

    def validate(self, data):
        data = super().validate(data)
        if not data["ec_prekeys"] or not data["kyber_prekeys"]:
            raise serializers.ValidationError("Initial one-time prekeys are required.")
        return data


class Publication(Keys):
    device_id = serializers.UUIDField()
    operation_id = serializers.UUIDField()


class Claim(StrictSerializer):
    device_id = serializers.UUIDField()
    recipient_device_id = serializers.UUIDField()
    operation_id = serializers.UUIDField()


class Envelope(Claim):
    kind = serializers.ChoiceField(choices=["message", "reaction", "delete", "receipt.delivered", "receipt.read", "receipt.played", "card.response"])
    target_id = serializers.UUIDField()
    reply_to = serializers.UUIDField(allow_null=True, default=None)
    version = serializers.IntegerField(min_value=0, max_value=9007199254740991, default=0)
    signal_type = serializers.ChoiceField(choices=[2, 3])
    ciphertext = serializers.CharField(max_length=87384, trim_whitespace=False)
    attachments = serializers.ListField(child=serializers.UUIDField(), max_length=3, default=list)

    def validate_ciphertext(self, value):
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise serializers.ValidationError("Invalid ciphertext encoding.") from None
        if not 32 <= len(decoded) <= 65536 or base64.b64encode(decoded).decode("ascii") != value:
            raise serializers.ValidationError("Ciphertext must contain 32–65536 bytes.")
        return value

    def validate(self, data):
        if len(set(data["attachments"])) != len(data["attachments"]) or (data["kind"] != "message" and data["attachments"]):
            raise serializers.ValidationError("Only messages can attach up to three different files.")
        if data["kind"] == "message":
            if data["target_id"] != data["operation_id"] or data["version"] != 0:
                raise serializers.ValidationError("Message identifier must equal its operation identifier, with version zero.")
        elif data["reply_to"] is not None or data["version"] < 1:
            raise serializers.ValidationError("Mutations require a positive version and no reply target.")
        return data


class SyncQuery(StrictSerializer):
    device_id = serializers.UUIDField()
    after = serializers.IntegerField(min_value=0, max_value=9007199254740991, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=50)


class AttachmentInit(StrictSerializer):
    device_id = serializers.UUIDField()
    attachment_id = serializers.UUIDField()
    message_id = serializers.UUIDField()
    ciphertext_size = serializers.IntegerField(min_value=29, max_value=16 * 1024 * 1024 + 28)
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$")


class AttachmentDevice(StrictSerializer):
    device_id = serializers.UUIDField()
