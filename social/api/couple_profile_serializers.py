from django.utils import timezone
from rest_framework import serializers
from social.services.couple_profile_sections import PROMPTS, SECTIONS


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        extra = set(data) - set(self.fields)
        if extra:
            raise serializers.ValidationError({key: "Unknown field." for key in sorted(extra)})
        return super().to_internal_value(data)


class SectionInput(StrictSerializer):
    expected_revision = serializers.CharField(min_length=64, max_length=64)


class StoryInput(SectionInput):
    answers = serializers.DictField(child=serializers.CharField(max_length=240, allow_blank=True), allow_empty=False)

    def validate_answers(self, answers):
        if set(answers) - set(PROMPTS):
            raise serializers.ValidationError("Unknown story prompt.")
        return answers


class SongInput(SectionInput):
    remove = serializers.BooleanField(default=False)
    title = serializers.CharField(max_length=120, required=False)
    artist = serializers.CharField(max_length=120, allow_blank=True, default="")
    band = serializers.CharField(max_length=120, allow_blank=True, default="")
    note = serializers.CharField(max_length=240, allow_blank=True, default="")

    def validate(self, data):
        if not data["remove"] and not data.get("title"):
            raise serializers.ValidationError({"title": "Enter a song name."})
        return data


class DateInput(SectionInput):
    anniversary_date = serializers.DateField(allow_null=True)
    def validate_anniversary_date(self, value):
        if value and value > timezone.localdate():
            raise serializers.ValidationError("Choose today or an earlier date.")
        return value


class InterestsInput(SectionInput):
    names = serializers.ListField(child=serializers.CharField(max_length=100), max_length=20)
    def validate_names(self, values):
        seen = set()
        result = []
        for value in values:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result


class SharingInput(SectionInput):
    action = serializers.ChoiceField(choices=["propose", "approve", "revoke"])
    sections = serializers.ListField(child=serializers.ChoiceField(choices=SECTIONS), max_length=6, required=False)
    def validate(self, data):
        if data["action"] == "propose" and "sections" not in data:
            raise serializers.ValidationError({"sections": "Choose which sections to share."})
        return data


class CoverInput(SectionInput):
    photo = serializers.ImageField(required=False)
    moment_photo_id = serializers.IntegerField(min_value=1, required=False)
    focal_y = serializers.FloatField(min_value=0, max_value=1, default=0.5)
    remove = serializers.BooleanField(default=False)
    def validate_focal_y(self, value):
        import math
        if not math.isfinite(value):
            raise serializers.ValidationError("Choose a valid position.")
        return value
    def validate_photo(self, value):
        from authentication.api.serializers import SetUpProfileSerializer
        return SetUpProfileSerializer().validate_profile_photo(value)
    def validate(self, data):
        if sum(bool(data.get(k)) for k in ("photo", "moment_photo_id", "remove")) > 1:
            raise serializers.ValidationError({"photo": "Choose one cover source."})
        return data


class PlanInput(StrictSerializer):
    request_id = serializers.UUIDField()
    title = serializers.CharField(max_length=120)
    starts_at = serializers.DateTimeField()


class PlanResponseInput(StrictSerializer):
    expected_revision = serializers.IntegerField(min_value=1)
    response = serializers.ChoiceField(choices=["yes", "no", "another_time"], required=False)
    response_note = serializers.CharField(max_length=240, allow_blank=True, default="")
    completed = serializers.BooleanField(required=False)
    def validate(self, data):
        if ("response" in data) == ("completed" in data):
            raise serializers.ValidationError("Choose one action.")
        return data


class StoryInviteInput(StrictSerializer):
    request_id = serializers.UUIDField()
    prompt = serializers.ChoiceField(choices=list(PROMPTS))
