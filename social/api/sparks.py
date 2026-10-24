from django.db import transaction
from django.http import Http404
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.models import CoupleMembershipModel, SparkSession
from social.services import sparks


class StartInput(StrictSerializer):
    request_id = serializers.UUIDField()
    couple = serializers.IntegerField(min_value=1)
    question_id = serializers.ChoiceField(choices=list(sparks.QUESTIONS))


class EnvelopeInput(StrictSerializer):
    algorithm = serializers.ChoiceField(choices=["m.megolm.v1.aes-sha2"])
    ciphertext = serializers.RegexField(r"^[A-Za-z0-9+/]+={0,2}$", min_length=128, max_length=16384)
    session_id = serializers.RegexField(r"^[A-Za-z0-9+/]{43}$")
    sender_key = serializers.RegexField(r"^[A-Za-z0-9+/]{43}$", required=False)
    device_id = serializers.CharField(min_length=1, max_length=255, required=False)


class AnswerInput(StrictSerializer):
    envelope = EnvelopeInput()


class CardInput(StrictSerializer):
    event_id = serializers.RegexField(r"^\$[^\s]{1,254}$")


class SparkThrottle(UserRateThrottle):
    scope = "sparks"
    rate = "120/hour"
    def allow_request(self, request, view):
        return request.method in ("GET", "HEAD", "OPTIONS") or super().allow_request(request, view)


class SparkView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [SparkThrottle]
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    @action(detail=False, methods=["get"])
    def home(self, request):
        membership = CoupleMembershipModel.objects.filter(user=request.user, ended_at__isnull=True).first()
        result = {"decks": sparks.CATALOGUE, "couple": None, "members": [], "recent": [], "active": None}
        if not membership:
            return Response({"data": result})
        try:
            couple, members = sparks.current_couple(request.user, membership.couple_id)
        except Http404:
            return Response({"data": result})
        result["couple"] = couple.pk
        result["members"] = [{"id": m.user_id, "name": m.user.full_name or "Your partner",
            "photo": request.build_absolute_uri(m.user.profile_photo.url) if m.user.profile_photo else None} for m in members]
        try:
            room, mapping, _ = sparks.room_for(couple, members)
        except sparks.SparkConflict:
            result["unavailable"] = "Secure chat is getting ready. Open chat, then try Sparks again."
            return Response({"data": result})
        items = SparkSession.objects.filter(couple=couple, audience_membership_ids=[m.pk for m in members],
            audience_user_ids=[m.user_id for m in members], matrix_room_id=mapping.matrix_room_id,
            membership_epoch=mapping.membership_epoch).prefetch_related("answers").order_by("-created_at")
        for spark in items[:30]:
            value = sparks.data(request.user, spark)
            value["answers"] = []  # Home/history never need answer ciphertext.
            if spark.state == "open":
                result["active"] = value
            else:
                result["recent"].append(value)
        return Response({"data": result})

    @action(detail=False, methods=["get"])
    def history(self, request):
        """Bounded, stable IDs for recovery backfill; never returns answer envelopes.

        Each answer still goes through retrieve's original-audience/reveal gate.
        UUID keyset pagination avoids offset drift when new Sparks are created.
        """
        membership = CoupleMembershipModel.objects.filter(user=request.user, ended_at__isnull=True).first()
        if not membership:
            raise Http404
        couple, members = sparks.current_couple(request.user, membership.couple_id)
        _, mapping, _ = sparks.room_for(couple, members)
        items = SparkSession.objects.filter(couple=couple,
            audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members],
            matrix_room_id=mapping.matrix_room_id, membership_epoch=mapping.membership_epoch,
        ).exclude(state="skipped").order_by("id")
        after = request.query_params.get("after")
        if after:
            items = items.filter(id__gt=serializers.UUIDField().run_validation(after))
        ids = list(items.values_list("id", flat=True)[:26])
        return Response({"data": {"ids": [str(pk) for pk in ids[:25]],
            "next": str(ids[24]) if len(ids) > 25 else None}})

    def create(self, request):
        value = StartInput(data=request.data)
        value.is_valid(raise_exception=True)
        spark, created = sparks.start(
            request.user, value.validated_data["request_id"], value.validated_data["couple"], value.validated_data["question_id"])
        return Response({"data": sparks.data(request.user, spark)}, status=201 if created else 200)

    def retrieve(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        spark, _ = sparks.session_for(request.user, pk)
        return Response({"data": sparks.data(request.user, spark)})

    @action(detail=True, methods=["put"])
    def answer(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        value = AnswerInput(data=request.data)
        value.is_valid(raise_exception=True)
        spark = sparks.submit(request.user, pk, value.validated_data["envelope"])
        return Response({"data": sparks.data(request.user, spark)})

    @action(detail=True, methods=["post"])
    def skip(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        value = StrictSerializer(data=request.data)
        value.is_valid(raise_exception=True)
        spark = sparks.skip(request.user, pk)
        return Response({"data": sparks.data(request.user, spark)})

    @action(detail=True, methods=["put"])
    def card(self, request, pk=None):
        pk = serializers.UUIDField().run_validation(pk)
        value = CardInput(data=request.data)
        value.is_valid(raise_exception=True)
        with transaction.atomic():
            spark, _ = sparks.session_for(request.user, pk, lock=True)
            if spark.creator_id != request.user.pk:
                raise Http404
            if not spark.card_event_id:
                spark.card_event_id = value.validated_data["event_id"]
                spark.save(update_fields=["card_event_id"])
        return Response({"data": sparks.data(request.user, spark)})
