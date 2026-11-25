import logging
from time import perf_counter

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from social.api.couple_pagination import decode_cursor, keyset, next_url, page_size, positive_int, SessionChanged
from social.api.moment_serializers import CoupleMomentSerializer
from social.api.moment_views import PrivateResponseMixin
from social.models import CoupleFaveModel, CoupleMomentViewModel
from social.services.couple_feed_service import load_session, serve_page, start_session, cards
from social.services.fave_service import add_fave, available_couples, remove_fave, fave_version
from social.services.discovery_identity import identities
from authentication.models import UserModel
from social.services.moment_service import eligible_moments, hydrate_moments

logger = logging.getLogger(__name__)


class FaveWriteThrottle(UserRateThrottle):
    scope = "couple_fave_writes"
    rate = "120/min"

    def allow_request(self, request, view):
        return request.method not in ("PUT", "DELETE") or super().allow_request(request, view)


class FeedRequestThrottle(UserRateThrottle):
    scope = "couple_feed_requests"
    rate = "120/min"


class FeedStartThrottle(UserRateThrottle):
    scope = "couple_feed_starts"
    rate = "30/min"

    def allow_request(self, request, view):
        return "cursor" in request.query_params or super().allow_request(request, view)


class CoupleAPI(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.COUPLE_FEED_ENABLED:
            raise Http404


def selection(request, state, field, default, choices):
    value = request.query_params.get(field, state[field] if state else default)
    if value not in choices or (state and value != state[field]):
        raise ValidationError({field: "Invalid value or changed pagination parameter."})
    return value


def envelope(results, next_link=None, **metadata):
    return Response({"message": "Results fetched successfully.",
                     "data": {**metadata, "next": next_link, "previous": None, "results": results}})


class CoupleFaveDetail(CoupleAPI):
    throttle_classes = [FaveWriteThrottle]

    def expected(self, request):
        value = request.headers.get("If-Match")
        if value is None:
            return None
        if not value.isascii() or not value.isdecimal() or len(value) > 18:
            raise ValidationError({"If-Match": "Use the current nonnegative Faves version."})
        return int(value)

    @transaction.atomic
    def get(self, request, couple_id):
        couple_id = positive_int(str(couple_id), "couple")
        UserModel.objects.select_for_update().get(pk=request.user.pk)
        return Response({"message": "Fave state fetched.", "data": {
            "couple_id": couple_id, "is_faved": CoupleFaveModel.objects.filter(user=request.user, couple_id=couple_id).exists(),
            "available": available_couples(request.user).filter(pk=couple_id).exists(),
            "version": fave_version(request.user)}})

    @transaction.atomic
    def put(self, request, couple_id):
        couple_id = positive_int(str(couple_id), "couple")
        if request.data:
            raise ValidationError({"body": "No body is accepted; Faves belong to the authenticated user."})
        fave, created = add_fave(request.user, couple_id, self.expected(request))
        return Response({"message": "Couple added to Faves.", "data": {
            "couple_id": fave.couple_id, "is_faved": True, "created_at": fave.created_at,
            "version": fave_version(request.user)}},
            status=201 if created else 200)

    def delete(self, request, couple_id):
        remove_fave(request.user, positive_int(str(couple_id), "couple"), self.expected(request))
        return Response(status=204)


class CoupleFaveList(CoupleAPI):
    def get(self, request):
        version = fave_version(request.user)
        state = decode_cursor(request, "faves")
        size = page_size(request, state)
        as_of = parse_datetime(state["as_of"]) if state else timezone.now()
        rows = list(keyset(CoupleFaveModel.objects.filter(user=request.user, created_at__lte=as_of),
                          state).order_by("-created_at", "-id")[:size + 1])
        more, rows = len(rows) > size, rows[:size]
        available = set(available_couples(request.user).filter(pk__in=[row.couple_id for row in rows])
                        .values_list("pk", flat=True))
        active = set(eligible_moments(request.user).filter(visibility="public", couple_id__in=available)
                     .values_list("couple_id", flat=True))
        results = [{"couple_id": row.couple_id, "is_faved": True, "created_at": row.created_at,
                    "available": row.couple_id in available, "has_active_content": row.couple_id in active}
                   for row in rows]
        public = identities(request, available)
        for row in results:
            if row["available"]:
                row["couple"] = public.get(row["couple_id"], {"id": row["couple_id"]})
        link = next_url(request, "faves", {"as_of": as_of.isoformat(), "size": size,
            "last_at": rows[-1].created_at.isoformat(), "last_id": rows[-1].pk}) if more else None
        if fave_version(request.user) != version:
            raise SessionChanged()
        return envelope(results, link, fave_version=version, server_time=timezone.now())


class CoupleFeedView(CoupleAPI):
    throttle_classes = [FeedRequestThrottle, FeedStartThrottle]

    def get(self, request):
        started = perf_counter()
        cursor = decode_cursor(request, "couple-feed")
        state = load_session(request.user, cursor["session"]) if cursor else None
        section = selection(request, state, "section", "global", {"global", "faves"})
        selection(request, state, "content", "moments", {"moments"})
        scope = selection(request, state, "scope", "local", {"local", "broad"})
        size = page_size(request, state)
        if state:
            session_id, position = cursor["session"], cursor["position"]
        else:
            session_id, state = start_session(request.user, section, size, scope)
            position = 0
        results, position, has_faves = serve_page(request, state, position)
        logger.info("couple_feed section=%s candidates=%d returned=%d elapsed_ms=%.1f version=%s cache=%s",
                    section, len(state["ids"]), len(results), (perf_counter() - started) * 1000,
                    state["ranking_version"], session_id is not None)
        link = next_url(request, "couple-feed", {"session": session_id, "position": position}) \
            if session_id and position < len(state["ids"]) else None
        empty_reason = None
        if not results:
            empty_reason = ("no_faves" if not has_faves else "no_active_fave_moments") \
                if section == "faves" else "no_active_moments"
        return envelope(results, link, section=section, content_type="moments", as_of=state["as_of"],
                        ranking_version=state["ranking_version"], empty_reason=empty_reason,
                        pagination_available=session_id is not None, server_time=timezone.now(),
                        scope=scope, coverage=state.get("coverage"), fave_version=int(state["fingerprint"].split(":")[0]))


class CoupleFeedCheck(CoupleAPI):
    """Reauthorize already-visible cards without reranking or moving the reading position."""
    throttle_classes = [FeedRequestThrottle]

    def get(self, request):
        version = fave_version(request.user)
        raw = request.query_params.get("couples", "").split(",")
        if not 1 <= len(raw) <= 50:
            raise ValidationError({"couples": "Check at most 50 couples."})
        ids = list(dict.fromkeys(positive_int(v, "couples") for v in raw))
        section = selection(request, None, "section", "global", {"global", "faves"})
        saved = CoupleFaveModel.objects.filter(user=request.user).values_list("couple_id", flat=True)
        results = cards(request, ids, timezone.now(), saved, section)
        live = set(eligible_moments(request.user).filter(visibility="public",
            pk__in=[row["preview"]["id"] for row in results.values()]).values_list("pk", flat=True))
        rows = [results[pk] for pk in ids if pk in results
                and results[pk]["preview"]["id"] in live
                and parse_datetime(results[pk]["preview"]["expires_at"]) > timezone.now()]
        if fave_version(request.user) != version:
            raise SessionChanged()
        return envelope(rows, server_time=timezone.now(), fave_version=version)


class CoupleMomentSequence(CoupleAPI):
    def get(self, request):
        state = decode_cursor(request, "moment-sequence")
        raw_couple = request.query_params.get("couple", str(state["couple"]) if state else None)
        couple_id = positive_int(raw_couple, "couple")
        if state and couple_id != state["couple"]:
            raise ValidationError({"couple": "Cannot change couple during pagination."})
        mode = selection(request, state, "mode", "unopened", {"unopened", "all"})
        creator_raw = request.query_params.get("creator", str(state.get("creator")) if state and state.get("creator") else None)
        creator = positive_int(creator_raw, "creator") if creator_raw is not None else None
        if state and creator != state.get("creator"):
            raise ValidationError({"creator": "Cannot change creator during pagination."})
        size = page_size(request, state)
        as_of = parse_datetime(state["as_of"]) if state else timezone.now()
        views = CoupleMomentViewModel.objects.filter(moment_id=OuterRef("pk"), viewer=request.user)
        queryset = eligible_moments(request.user).filter(couple_id=couple_id, created_at__lte=as_of)
        if creator is not None:
            queryset = queryset.filter(created_by_id=creator)
        queryset = queryset.annotate(has_opened=Exists(views))
        if mode == "unopened":
            queryset = queryset.filter(has_opened=False)
        queryset = keyset(queryset, state, ascending=True)
        rows = list(hydrate_moments(queryset, request.user).order_by("created_at", "id")[:size + 1])
        more, rows = len(rows) > size, rows[:size]
        # Eligibility is checked again after related data has been loaded.
        allowed = set(eligible_moments(request.user).filter(pk__in=[row.pk for row in rows]).values_list("pk", flat=True))
        results = []
        for row in rows:
            if row.pk in allowed and row.expires_at > timezone.now():
                data = CoupleMomentSerializer(row, context={"request": request}).data
                if row.expires_at > timezone.now():
                    results.append({**data, "has_opened": row.has_opened})
        link = next_url(request, "moment-sequence", {"couple": couple_id, "mode": mode, "creator": creator,
            "as_of": as_of.isoformat(), "size": size, "last_at": rows[-1].created_at.isoformat(),
            "last_id": rows[-1].pk}) if more else None
        return envelope(results, link, couple_id=couple_id, mode=mode, as_of=as_of, server_time=timezone.now(),
                        empty_reason="no_unopened_moments" if not results and mode == "unopened" else None)
