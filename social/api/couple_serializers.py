"""Deliberately limited external cards: no member-only couple profile fields."""
from rest_framework import serializers

from social.api.moment_serializers import CoupleMomentPhotoSerializer
from social.models import CoupleMomentModel


class MomentPreviewSerializer(serializers.ModelSerializer):
    photos = CoupleMomentPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = CoupleMomentModel
        fields = ["id", "title", "caption", "created_at", "expires_at", "photos"]
        read_only_fields = fields
