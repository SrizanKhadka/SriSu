from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.api.plans import PlanFields, PlanThrottle
from social.services import date_ideas


class CreateIdea(StrictSerializer):
    request_id = serializers.UUIDField()
    couple = serializers.IntegerField(min_value=1)
    preset = serializers.ChoiceField(choices=date_ideas.PRESETS)


class Interest(StrictSerializer):
    interested = serializers.BooleanField()


class IdeaAction(StrictSerializer):
    request_id = serializers.UUIDField()
    expected_revision = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=("interest", "withdraw", "plan"))
    values = serializers.DictField(default=dict)

    def validate(self, data):
        child = (PlanFields if data["action"] == "plan" else Interest if data["action"] == "interest" else StrictSerializer)(data=data["values"])
        child.is_valid(raise_exception=True)
        data["values"] = child.validated_data
        return data


class DateIdeaView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [PlanThrottle]
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def list(self, request):
        couple_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get("couple"))
        couple, members = date_ideas.current_couple(request.user, couple_id)
        # One live idea per author. Converted/withdrawn IDs remain retrievable.
        rows = date_ideas.eligible(couple, members).filter(state="open").order_by("created_at", "id")[:2]
        return Response({"data": {"couple": couple.pk, "items": [date_ideas.data(i) for i in rows]}})

    def create(self, request):
        value = CreateIdea(data=request.data); value.is_valid(raise_exception=True)
        fields = value.validated_data
        idea, created = date_ideas.create(request.user, fields["request_id"], fields["couple"], fields["preset"])
        return Response({"data": date_ideas.data(idea)}, status=201 if created else 200)

    def retrieve(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        return Response({"data": date_ideas.data(date_ideas.idea_for(request.user, pk)[0])})

    @action(detail=True, methods=["post"], url_path="actions")
    def actions(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        value = IdeaAction(data=request.data); value.is_valid(raise_exception=True)
        return Response({"data": date_ideas.data(date_ideas.act(request.user, pk, **value.validated_data))})
