import json
from django.conf import settings
from django.utils import timezone
from django.urls import reverse
from rest_framework import serializers
from social.models import (CoupleMomentModel, CoupleMomentPhotoModel, CoupleMomentNoteModel,
                           CoupleMomentNoteReplyModel)


class PhotoIDsField(serializers.JSONField):
    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                self.fail("invalid")
        if not isinstance(data, list) or any(type(item) is not int or item < 1 for item in data):
            raise serializers.ValidationError("Must be an array of positive integer photo IDs.")
        if len(data) > 5 or len(set(data)) != len(data):
            raise serializers.ValidationError("Provide at most five unique photo IDs.")
        return data


class MomentImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if getattr(data, "size", 0) > getattr(settings, "MOMENT_MAX_IMAGE_BYTES", 10 * 1024 * 1024):
            raise serializers.ValidationError("Image exceeds the size limit (default 10 MiB).")
        image = super().to_internal_value(data)
        if image.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise serializers.ValidationError("Only JPEG, PNG and WebP images are supported.")
        return image


class CoupleMomentPhotoSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = CoupleMomentPhotoModel
        fields = ["id", "image", "order", "uploaded_at"]

    def get_image(self, obj):
        path = reverse("couple_moments-photo", args=[obj.moment_id, obj.id])
        return self.context["request"].build_absolute_uri(path)


class CoupleIDField(serializers.IntegerField):
    def to_representation(self, value):
        return value.pk


class CoupleMomentSerializer(serializers.ModelSerializer):
    couple = CoupleIDField(min_value=1, max_value=9223372036854775807, required=False)
    photos = serializers.ListField(child=MomentImageField(), max_length=5, required=False, write_only=True)
    replace_photos = serializers.BooleanField(required=False, write_only=True)
    deleted_photo_ids = PhotoIDsField(required=False, write_only=True)
    caption = serializers.CharField(max_length=1000, allow_blank=True, required=False)
    moment_date = serializers.DateField(default=timezone.localdate)
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    total_view_count = serializers.SerializerMethodField()

    class Meta:
        model = CoupleMomentModel
        fields = ["id", "couple", "created_by", "title", "caption", "moment_date", "mood",
                  "location_name", "visibility", "tags", "partner_memory", "photos",
                  "created_at", "updated_at", "expires_at", "can_edit", "can_delete",
                  "replace_photos", "deleted_photo_ids", "total_view_count"]
        read_only_fields = ["id", "created_by", "created_at", "updated_at", "expires_at"]

    def validate(self, attrs):
        if self.instance and "couple" in attrs and attrs["couple"] != self.instance.couple_id:
            raise serializers.ValidationError({"couple": "A moment cannot be moved to another couple."})
        if attrs.get("visibility") == "friends":
            raise serializers.ValidationError({"visibility": "Friends visibility is not supported; use private or public."})
        if "tags" in attrs:
            tags = attrs["tags"]
            if not isinstance(tags, list) or len(tags) > 20 or any(
                not isinstance(tag, str) or not tag.strip() or len(tag) > 50 for tag in tags
            ):
                raise serializers.ValidationError({"tags": "Provide up to 20 nonblank strings of at most 50 characters."})
        if self.instance is None and (attrs.get("replace_photos") or attrs.get("deleted_photo_ids")):
            raise serializers.ValidationError("Photo replacement/deletion is only valid on updates.")
        return attrs

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result["photos"] = CoupleMomentPhotoSerializer(instance.photos.all(), many=True, context=self.context).data
        request = self.context["request"]
        if request.method == "GET" and any(
            member.user_id == request.user.id and member.id in instance.audience_membership_ids
            and member.user_id in instance.audience_user_ids for member in instance.couple.memberships.all()
        ):
            result["appreciation_notes"] = MomentNoteSerializer(
                instance.appreciation_notes, many=True, context=self.context).data
        return result

    def get_total_view_count(self, obj):
        if hasattr(obj, "total_view_count"):
            return obj.total_view_count
        return obj.views.count()

    def get_can_edit(self, obj):
        return obj.created_by_id == self.context["request"].user.id

    def get_can_delete(self, obj):
        return self.get_can_edit(obj)


class MomentNoteReplySerializer(serializers.ModelSerializer):
    message = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)

    class Meta:
        model = CoupleMomentNoteReplyModel
        fields = ["id", "note", "author", "message", "created_at"]
        read_only_fields = ["id", "note", "author", "created_at"]


class MomentNoteSerializer(serializers.ModelSerializer):
    message = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)
    replies = MomentNoteReplySerializer(many=True, read_only=True)

    class Meta:
        model = CoupleMomentNoteModel
        fields = ["id", "moment", "sender", "message", "created_at", "replies"]
        read_only_fields = ["id", "moment", "sender", "created_at"]
