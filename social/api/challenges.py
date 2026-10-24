from django.db import transaction
from django.db.models import Q
from django.http import Http404
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.models import CoupleMembershipModel
from social.services import challenges


class StartInput(StrictSerializer):
    request_id = serializers.UUIDField()
    couple = serializers.IntegerField(min_value=1)
    template_id = serializers.CharField(max_length=48)


class ActionInput(StrictSerializer):
    request_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=challenges.ACTIONS)
    expected_revision = serializers.IntegerField(min_value=1)


class CardInput(StrictSerializer):
    event_id = serializers.RegexField(r"^\$[^\s]{1,254}$")


class ChallengeThrottle(UserRateThrottle):
    scope = "challenges"
    rate = "120/hour"
    def allow_request(self, request, view):
        return request.method in ("GET", "HEAD", "OPTIONS") or super().allow_request(request, view)


class ChallengeView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [ChallengeThrottle]
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def audience(self, request):
        membership = CoupleMembershipModel.objects.filter(user=request.user, ended_at__isnull=True).first()
        if not membership:
            raise Http404
        return challenges.current_couple(request.user, membership.couple_id)

    @action(detail=False, methods=["get"])
    def home(self, request):
        result = {"collections": challenges.catalogue(), "couple": None, "members": [], "current": None}
        try:
            couple, members = self.audience(request)
        except Http404:
            return Response({"data": result})
        result["couple"] = couple.pk
        result["members"] = [{"id": m.user_id, "name": m.user.full_name or "Your partner",
            "photo": request.build_absolute_uri(m.user.profile_photo.url) if m.user.profile_photo else None} for m in members]
        current = challenges.eligible(couple, members).filter(state__in=challenges.LIVE).first()
        result["current"] = challenges.data(current) if current else None
        return Response({"data": result})

    @action(detail=False, methods=["get"])
    def history(self, request):
        couple, members = self.audience(request)
        all_items = challenges.eligible(couple, members).exclude(state__in=challenges.LIVE)
        items = all_items.order_by("-created_at", "-id")
        before = request.query_params.get("before")
        if before is not None:
            pk = serializers.UUIDField().run_validation(before)
            anchor = all_items.filter(pk=pk).first()
            if not anchor:
                raise Http404
            items = items.filter(Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, pk__lt=anchor.pk))
        page = list(items[:21])
        return Response({"data": {"items": [challenges.data(c) for c in page[:20]],
            "next": str(page[19].pk) if len(page) > 20 else None}})

    def create(self, request):
        value = StartInput(data=request.data); value.is_valid(raise_exception=True)
        challenge, created = challenges.start(
            request.user, value.validated_data["request_id"], value.validated_data["couple"], value.validated_data["template_id"])
        return Response({"data": challenges.data(challenge)}, status=201 if created else 200)

    def retrieve(self, request, pk=None):
        return Response({"data": challenges.data(challenges.challenge_for(request.user, serializers.UUIDField().run_validation(pk)))})

    @action(detail=True, methods=["post"], url_path="actions")
    def actions(self, request, pk=None):
        value = ActionInput(data=request.data); value.is_valid(raise_exception=True)
        instance = challenges.act(request.user, serializers.UUIDField().run_validation(pk), **value.validated_data)
        return Response({"data": challenges.data(instance)})

    @action(detail=True, methods=["put"])
    def card(self, request, pk=None):
        value = CardInput(data=request.data); value.is_valid(raise_exception=True)
        with transaction.atomic():
            instance = challenges.challenge_for(request.user, serializers.UUIDField().run_validation(pk), lock=True)
            if instance.creator_id != request.user.pk:
                raise Http404
            if not instance.card_event_id:
                instance.card_event_id = value.validated_data["event_id"]
                instance.save(update_fields=["card_event_id"])
        return Response({"data": challenges.data(instance)})
