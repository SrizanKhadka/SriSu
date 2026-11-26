"""Deliberately limited external cards: no member-only couple profile fields."""
from rest_framework import serializers

from social.api.moment_serializers import CoupleMomentPhotoSerializer
from social.models import CoupleMomentModel


class MomentPreviewSerializer(serializers.ModelSerializer):
    photos = CoupleMomentPhotoSerializer(many=True, read_only=True)
    media_kind = serializers.SerializerMethodField()
    poster_url = serializers.SerializerMethodField()

    def get_media_kind(self, obj):
        kinds = {"video" if photo.video_id else "photo" for photo in obj.photos.all()}
        return next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "text"

    def get_poster_url(self, obj):
        photo = next(iter(obj.photos.all()), None)
        return CoupleMomentPhotoSerializer(photo, context=self.context).data["image"] if photo else None

    class Meta:
        model = CoupleMomentModel
        fields = ["id", "title", "caption", "created_at", "expires_at", "photos", "media_kind", "poster_url"]
        read_only_fields = fields
