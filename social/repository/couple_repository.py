"""Moment candidates supply content features to the independent couple scorer."""
from django.conf import settings
from django.db.models import CharField, Exists, OuterRef, Prefetch, Subquery, Value
from django.db.models.functions import Cast, Concat, MD5
import hashlib
import json

from authentication.models import UserInterestModel
from social.models import CoupleMembershipModel, CoupleMomentViewModel, CoupleFaveModel
from social.services.couple_scorer import DEFAULT_WEIGHTS, compose, interest_set, rank_score, normalize
from social.services.fave_service import available_couples
from social.services.moment_service import eligible_moments


def discovery_moments(user, as_of):
    own = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values("couple_id")
    return eligible_moments(user).filter(visibility="public", created_at__lte=as_of).exclude(couple_id__in=own)


def interest_names(user):
    return [item.interest.name if item.interest_id else item.name
            for item in user.discovery_interests]


def source_interests(user):
    rows = UserInterestModel.objects.filter(user=user, removed=False).select_related("interest")
    interests = interest_set([item.interest.name if item.interest_id else item.name for item in rows])
    if not interests:
        membership = CoupleMembershipModel.objects.select_related("couple").filter(user=user).first()
        interests = interest_set(membership.couple.shared_interests) if membership else set()
    return interests


def discovery_context(user):
    """Invalidate an old Global order when the requester's explicit preferences change."""
    value = [normalize(user.country), normalize(user.city), sorted(source_interests(user))]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def ranked_couples(user, section, as_of, faved_ids, scope="local", with_context=False):
    moments = discovery_moments(user, as_of)
    latest = moments.filter(couple_id=OuterRef("pk")).order_by("-created_at", "-id")
    views = CoupleMomentViewModel.objects.filter(moment_id=OuterRef("pk"), viewer=user)
    unopened = moments.alias(opened=Exists(views)).filter(opened=False, couple_id=OuterRef("pk"))
    candidates = available_couples(user).filter(pk__in=moments.values("couple_id")).annotate(
        latest_at=Subquery(latest.values("created_at")[:1]), has_unopened=Exists(unopened))
    if section == "faves":
        ids = list(candidates.filter(pk__in=faved_ids).order_by(
            "-has_unopened", "-latest_at", "pk").values_list("pk", flat=True))
        return (ids, {}, "saved") if with_context else ids

    # Sampling is per distinct couple, independent of upload volume or popularity.
    seed = f"{user.pk}:{as_of.date().isoformat()}"
    candidates = candidates.annotate(sample_key=MD5(Concat(Value(seed + ":"), Cast("pk", CharField()))))
    interests = UserInterestModel.objects.filter(removed=False).select_related("interest")
    # Select geographic tiers before the cap, so worldwide sampling cannot starve local supply.
    local_members = CoupleMembershipModel.objects.filter(couple_id=OuterRef("pk"), ended_at__isnull=True)
    country = (user.country or "").strip()
    city = (user.city or "").strip()
    candidates = candidates.annotate(
        same_country=Exists(local_members.filter(user__country__iexact=country)) if country else Value(False),
        same_city=Exists(local_members.filter(user__country__iexact=country, user__city__iexact=city)) if country and city else Value(False))
    limit = settings.COUPLE_FEED_CANDIDATE_LIMIT
    chosen = []
    tiers = ([candidates.filter(same_city=True), candidates.filter(same_city=False, same_country=True),
              candidates.filter(same_country=False)] if scope == "local" and country else [candidates])
    for tier in tiers:
        if len(chosen) >= limit:
            break
        chosen.extend(tier.order_by("sample_key", "pk").values_list("pk", flat=True)[:limit-len(chosen)])
    candidates = list(candidates.filter(pk__in=chosen).prefetch_related(
        "memberships__user",
        Prefetch("memberships__user__user_interests", queryset=interests, to_attr="discovery_interests")))
    # Only annotate the bounded candidate pool, never materialize the entire saved library.
    faved_ids = set(CoupleFaveModel.objects.filter(user=user, couple_id__in=chosen).values_list("couple_id", flat=True))
    own_interests = source_interests(user)
    ranked = []
    reasons = {}
    for couple in candidates:
        # The related manager uses ActiveCoupleMembershipManager; `.all()` also
        # consumes the bounded prefetch above instead of issuing per-card reads.
        members = [membership.user for membership in couple.memberships.all()]
        tags = interest_set(couple.shared_interests)
        if not tags:
            tags = interest_set([name for member in members for name in interest_names(member)])
        score = rank_score(own_interests, tags, (user.city, user.country),
                           [(member.city, member.country) for member in members],
                           couple.pk in faved_ids, couple.latest_at, as_of,
                           getattr(settings, "COUPLE_FEED_WEIGHTS", DEFAULT_WEIGHTS), normalize_missing=True)
        tier = 0 if couple.same_city else 1 if couple.same_country else 2
        ranked.append((tier if scope == "local" else 0, score, couple.latest_at, couple.pk))
        reasons[str(couple.pk)] = ("From your selected city" if couple.same_city else
            "From your selected country" if couple.same_country else
            "Based on your interests" if own_interests & tags else "Discover beyond your area" if country else "A fresh public Moment")
    ranked.sort(key=lambda item: (item[0], -item[1], -item[2].timestamp(), item[3]))
    ids = []
    for tier in sorted({item[0] for item in ranked}):
        ids.extend(compose([item[3] for item in ranked if item[0] == tier], faved_ids, seed,
                           settings.COUPLE_FEED_EXPLORATION_INTERVAL))
    coverage = "broader" if scope == "broad" or not country else (
        "local_with_fallback" if any(item[0] > (0 if city else 1) for item in ranked) else "local")
    return (ids, reasons, coverage) if with_context else ids
