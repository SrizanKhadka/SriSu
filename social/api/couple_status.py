from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.api.plans import PlanThrottle
from social.models import CoupleStatus
from social.services.moment_publication import current_couple
from social.services.plans import PlanConflict

STATUSES = ("Feeling cosy", "Thinking of you", "Could use a hug", "A little busy", "")


class StatusInput(StrictSerializer):
    couple = serializers.IntegerField(min_value=1)
    request_id = serializers.UUIDField()
    expected_revision = serializers.IntegerField(min_value=0)
    text = serializers.ChoiceField(choices=STATUSES, allow_blank=True)


def data(status):
    active = status.expires_at is not None and status.expires_at > timezone.now()
    return {"author": status.author_id, "text": status.text if active else "",
        "expires_at": status.expires_at if active else None, "revision": status.revision}


class CoupleStatusView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [PlanThrottle]

    def list(self, request):
        couple_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get("couple"))
        couple, members = current_couple(request.user, couple_id)
        rows = CoupleStatus.objects.filter(couple=couple, audience_membership_ids=[m.pk for m in members])
        return Response({"data": {"couple": couple.pk, "items": [data(s) for s in rows], "server_time": timezone.now()}})

    @action(detail=False, methods=["put"], url_path="mine")
    def mine(self, request):
        value = StatusInput(data=request.data); value.is_valid(raise_exception=True)
        fields = value.validated_data
        with transaction.atomic():
            couple, members = current_couple(request.user, fields["couple"], lock=True)
            audience = [m.pk for m in members]
            status, _ = CoupleStatus.objects.get_or_create(couple=couple, author=request.user,
                defaults={"audience_membership_ids": audience})
            if status.audience_membership_ids != audience:
                status.audience_membership_ids = audience
                status.revision = 0
                status.request_id = None
            payload = {"text": fields["text"], "expected_revision": fields["expected_revision"]}
            if status.request_id == fields["request_id"]:
                if status.request_payload != payload:
                    raise PlanConflict("This request was used for another status.")
            else:
                if status.revision != fields["expected_revision"]:
                    raise PlanConflict("Your status changed. Refresh before saving.")
                status.text = fields["text"]
                status.expires_at = timezone.now() + timedelta(hours=24) if status.text else None
                status.revision += 1
                status.request_id = fields["request_id"]
                status.request_payload = payload
                status.save()
        return Response({"data": data(status)})
