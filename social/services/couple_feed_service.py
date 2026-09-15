"""Stable couple order in Redis; PostgreSQL remains the access authority."""
import logging
import uuid

from django.conf import settings
from django.core.cache import caches
from django.db.models import Count, Exists, OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from redis.exceptions import RedisError

from social.api.couple_pagination import SessionChanged, SessionExpired
from social.api.couple_serializers import MomentPreviewSerializer
from social.models import CoupleModel, CoupleMomentViewModel
from social.repository.couple_repository import discovery_moments, ranked_couples
from social.services.fave_service import fave_state

logger = logging.getLogger(__name__)
CACHE_ERRORS = (RedisError, OSError)


def session_cache():
    return caches["couple_feed"]


def start_session(user, section, size):
    as_of = timezone.now()
    faved_ids, fingerprint = fave_state(user)
    ids = ranked_couples(user, section, as_of, faved_ids)
    state = {"user": user.pk, "section": section, "content": "moments", "size": size,
             "as_of": as_of.isoformat(), "fingerprint": fingerprint, "ids": ids,
             "ranking_version": settings.COUPLE_FEED_RANKING_VERSION}
    session_id = uuid.uuid4().hex
    try:
        session_cache().set(session_id, state, timeout=settings.COUPLE_FEED_SESSION_TTL)
    except CACHE_ERRORS:
        logger.warning("Couple feed session storage unavailable; serving first page only.")
        session_id = None
    return session_id, state


def load_session(user, session_id):
    try:
        state = session_cache().get(session_id)
    except CACHE_ERRORS:
        raise SessionExpired()
    if not state or state["user"] != user.pk:
        raise SessionExpired()
    return state


def cards(request, ids, as_of, faved_ids, section):
    moments = discovery_moments(request.user, as_of)
    if section == "faves":
        moments = moments.filter(couple_id__in=faved_ids)
    per_couple = moments.filter(couple_id=OuterRef("pk"))
    counts = per_couple.order_by().values("couple_id").annotate(total=Count("pk"))
    views = CoupleMomentViewModel.objects.filter(moment_id=OuterRef("pk"), viewer=request.user)
    unopened = per_couple.alias(opened=Exists(views)).filter(opened=False)
    summaries = list(CoupleModel.objects.filter(pk__in=ids).annotate(
        preview_id=Subquery(per_couple.order_by("-created_at", "-id").values("pk")[:1]),
        active_count=Subquery(counts.values("total")[:1]), has_unopened=Exists(unopened),
    ).filter(preview_id__isnull=False))
    # Rebuild the eligibility query at hydration time, rather than trusting cached IDs.
    previews = {moment.pk: moment for moment in discovery_moments(request.user, as_of).filter(
        pk__in=[couple.preview_id for couple in summaries]).prefetch_related("photos")}
    result = {}
    for couple in summaries:
        preview = previews.get(couple.preview_id)
        if preview is None or preview.expires_at <= timezone.now():
            continue
        data = MomentPreviewSerializer(preview, context={"request": request}).data
        if preview.expires_at <= timezone.now():
            continue
        result[couple.pk] = {"couple": {"id": couple.pk}, "is_faved": couple.pk in faved_ids,
            "has_unopened": couple.has_unopened, "active_moment_count": couple.active_count,
            "preview": data, "moments_url": request.build_absolute_uri(reverse("couple-moment-sequence"))
            + f"?couple={couple.pk}&mode=unopened"}
    return result


def serve_page(request, state, position):
    faved_ids, fingerprint = fave_state(request.user)
    if fingerprint != state["fingerprint"]:
        raise SessionChanged()
    ids, size = state["ids"], state["size"]
    results = []
    while position < len(ids) and len(results) < size:
        batch = ids[position:position + size - len(results)]
        available = cards(request, batch, parse_datetime(state["as_of"]), faved_ids, state["section"])
        results.extend(available[pk] for pk in batch if pk in available)
        position += len(batch)
    # A mutation while the page was being assembled must not return stale Faves.
    if fave_state(request.user)[1] != fingerprint:
        raise SessionChanged()
    # Earlier cards may have become unavailable while later batches were scanned.
    # Filter once more at delivery; a short page is preferable to stale authorization.
    live_ids = set(discovery_moments(request.user, parse_datetime(state["as_of"])).filter(
        pk__in=[card["preview"]["id"] for card in results]).values_list("pk", flat=True))
    results = [card for card in results if card["preview"]["id"] in live_ids
               and parse_datetime(card["preview"]["expires_at"]) > timezone.now()]
    return results, position, bool(faved_ids)
