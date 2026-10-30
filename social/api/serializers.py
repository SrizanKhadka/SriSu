from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel, CoupleMomentModel, CoupleMomentPhotoModel, PhotoAlbumModel, SingleConnectionModel, UserPreferenceModel
from social.services.couple_profile_service import (
    CoupleProfileConflict,
    create_or_get_couple_for_connection,
)
from authentication.api.serializers import UserPhotoSerializer, UserInterestSerializer
from authentication.models import UserModel
from social.services.phone_identity import (
    is_number_same,
    is_number_valid,
    user_with_number_exists,
)
from datetime import date
from utils.choices import CoupleConnectionStatus, SingleConnectionStatus


class CoupleConnectionPartnerSerializer(serializers.ModelSerializer):
    profile_photo = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = ["id", "full_name", "username", "profile_photo"]
        read_only_fields = fields

    def get_profile_photo(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.profile_photo.url) if request else obj.profile_photo.url


class PartnerDiscoverySerializer(CoupleConnectionPartnerSerializer):
    """Minimal exact-phone lookup projection used before an invitation."""

    class Meta(CoupleConnectionPartnerSerializer.Meta):
        fields = ["id", "full_name", "username", "phone_number", "profile_photo"]
        read_only_fields = fields


class CoupleConnectionSerializer(serializers.ModelSerializer):
    
    partner = serializers.SerializerMethodField()
    class Meta:
        model = CoupleConnectionModel
        # The stored acceptance idempotency key is an internal replay guard,
        # not relationship/profile data, and must never cross the API boundary.
        fields = [
            "id",
            "sender_number",
            "receiver_number",
            "connection_status",
            "breakup_reason",
            "accepted_at",
            "ended_at",
            "revision",
            "created_at",
            "updated_at",
            "partner",
        ]
        read_only_fields = (
            "accepted_at",
            "ended_at",
            "revision",
            "created_at",
            "updated_at",
        )

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
        if not request or not hasattr(request, "user"):
            return None

        current_user = request.user
        try:
            if current_user.phone_number == obj.sender_number:
                partner_user = UserModel.objects.get(phone_number=obj.receiver_number)
            else:
                partner_user = UserModel.objects.get(phone_number=obj.sender_number)

            return CoupleConnectionPartnerSerializer(
                partner_user,
                context=self.context,
            ).data
        except UserModel.DoesNotExist:
            return None
        



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

class CoupleProfileUserSerializer(serializers.ModelSerializer):
    profile_photo = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = ["id", "full_name", "username", "phone_number", "profile_photo"]
        read_only_fields = fields

    def get_profile_photo(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.profile_photo.url) if request else obj.profile_photo.url


class CoupleMembershipSerializer(serializers.ModelSerializer):
    user = CoupleProfileUserSerializer(read_only=True)

    class Meta:
        model = CoupleMembershipModel
        fields = ["id", "position", "nickname", "is_owner", "joined_at", "user"]
        read_only_fields = fields


class CoupleModelSerializer(serializers.ModelSerializer):
    partner_id = serializers.PrimaryKeyRelatedField(
        queryset=UserModel.objects.filter(is_active=True),
        source="partner",
        write_only=True,
        required=False,
    )
    members = CoupleMembershipSerializer(source="memberships", many=True, read_only=True)
    partner = serializers.SerializerMethodField()
    days_together = serializers.SerializerMethodField()
    cover_photo_url = serializers.SerializerMethodField()
    profile_complete = serializers.SerializerMethodField()

    class Meta:
        model = CoupleModel
        fields = [
            "id",
            "couple_connection",
            "partner_id",
            "members",
            "partner",
            "title",
            "anniversary_date",
            "days_together",
            "shared_dreams",
            "shared_interests",
            "relationship_tagline",
            "journey_story",
            "relationship_strength",
            "cover_photo",
            "cover_photo_url",
            "profile_complete",
            "profile_completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "couple_connection",
            "members",
            "partner",
            "days_together",
            "cover_photo_url",
            "profile_complete",
            "profile_completed_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication is required.")

        self._validate_string_list(attrs, "shared_interests")
        self._validate_string_list(attrs, "shared_dreams")

        anniversary_date = attrs.get("anniversary_date")
        if anniversary_date and anniversary_date > date.today():
            raise serializers.ValidationError(
                {"anniversary_date": "Anniversary date cannot be in the future."}
            )

        if self.instance:
            if "partner" in attrs:
                raise serializers.ValidationError(
                    {"partner_id": "The partner cannot be changed on an existing profile."}
                )
            if not self.instance.memberships.filter(user=request.user, ended_at__isnull=True).exists():
                raise serializers.ValidationError(
                    "You cannot update a couple profile you do not belong to."
                )
            return attrs

        partner = attrs.get("partner")
        if not partner:
            raise serializers.ValidationError({"partner_id": "This field is required."})
        if partner.id == request.user.id:
            raise serializers.ValidationError({"partner_id": "You cannot pair with yourself."})
        if not request.user.is_active or not partner.is_active:
            raise serializers.ValidationError(
                {"partner_id": "Both users must be active."}
            )

        connection = CoupleConnectionModel.objects.filter(
            Q(sender_number=request.user.phone_number, receiver_number=partner.phone_number)
            | Q(sender_number=partner.phone_number, receiver_number=request.user.phone_number),
            connection_status=CoupleConnectionStatus.ACCEPTED,
        ).first()
        if not connection:
            raise serializers.ValidationError(
                {"partner_id": "An accepted couple connection is required."}
            )

        self._connection = connection
        return attrs

    @staticmethod
    def _validate_string_list(attrs, field_name):
        value = attrs.get(field_name)
        if value is None:
            return
        if not isinstance(value, list):
            raise serializers.ValidationError({field_name: "Must be a list."})
        if len(value) > 20:
            raise serializers.ValidationError(
                {field_name: "A maximum of 20 items is allowed."}
            )
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise serializers.ValidationError(
                {field_name: "Every item must be a non-empty string."}
            )
        attrs[field_name] = list(dict.fromkeys(item.strip() for item in value))

    def create(self, validated_data):
        validated_data.pop("partner")
        try:
            couple = create_or_get_couple_for_connection(self._connection)
        except CoupleProfileConflict as exc:
            raise serializers.ValidationError(str(exc)) from exc

        if couple.profile_completed_at:
            raise serializers.ValidationError("A couple profile already exists.")

        for field, value in validated_data.items():
            setattr(couple, field, value)
        couple.profile_completed_at = timezone.now()
        couple.save()
        return couple

    def validate_cover_photo(self, value):
        from authentication.api.serializers import SetUpProfileSerializer
        return SetUpProfileSerializer().validate_profile_photo(value)

    def update(self, instance, validated_data):
        # Legacy writes must not save a stale full model over revisions/consent.
        from social.services.moment_service import queue_file_deletion
        fields = list(validated_data)
        old = instance.cover_photo.name
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if "cover_photo" in fields:
            instance.cover_source_moment_photo = None
            fields.append("cover_source_moment_photo")
        instance.save(update_fields=fields + ["updated_at"])
        if "cover_photo" in fields and old and old != instance.cover_photo.name:
            queue_file_deletion(old)
        return instance

    def to_representation(self, instance):
        result = super().to_representation(instance)
        if instance.cover_photo or instance.cover_source_moment_photo_id:
            request = self.context.get("request")
            path = f"/api/social/profiles/{instance.pk}/cover/"
            result["cover_photo"] = request.build_absolute_uri(path) if request else path
            result["cover_photo_url"] = result["cover_photo"]
        return result

    def get_partner(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        membership = obj.memberships.filter(ended_at__isnull=True).exclude(user=request.user).select_related("user").first()
        if not membership:
            return None
        return CoupleProfileUserSerializer(membership.user, context=self.context).data

    @staticmethod
    def get_days_together(obj):
        if not obj.anniversary_date:
            return None
        return max((date.today() - obj.anniversary_date).days, 0)

    def get_cover_photo_url(self, obj):
        if not obj.cover_photo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.cover_photo.url) if request else obj.cover_photo.url

    @staticmethod
    def get_profile_complete(obj):
        return obj.profile_completed_at is not None

# Kept as import aliases for existing callers.
from social.api.moment_serializers import CoupleMomentPhotoSerializer, CoupleMomentSerializer
