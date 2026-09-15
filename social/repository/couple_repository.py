"""Moment candidates supply content features to the independent couple scorer."""
from django.conf import settings
from django.db.models import CharField, Exists, OuterRef, Prefetch, Subquery, Value
from django.db.models.functions import Cast, Concat, MD5

from authentication.models import UserInterestModel
from social.models import CoupleMembershipModel, CoupleMomentViewModel
from social.services.couple_scorer import DEFAULT_WEIGHTS, compose, interest_set, rank_score
from social.services.fave_service import available_couples
from social.services.moment_service import eligible_moments


def discovery_moments(user, as_of):
    own = CoupleMembershipModel.objects.filter(user=user).values("couple_id")
    return eligible_moments(user).filter(visibility="public", created_at__lte=as_of).exclude(couple_id__in=own)


def interest_names(user):
    return [item.interest.name if item.interest_id else item.name
            for item in user.discovery_interests]


def ranked_couples(user, section, as_of, faved_ids):
    moments = discovery_moments(user, as_of)
    latest = moments.filter(couple_id=OuterRef("pk")).order_by("-created_at", "-id")
    views = CoupleMomentViewModel.objects.filter(moment_id=OuterRef("pk"), viewer=user)
    unopened = moments.alias(opened=Exists(views)).filter(opened=False, couple_id=OuterRef("pk"))
    candidates = available_couples(user).filter(pk__in=moments.values("couple_id")).annotate(
        latest_at=Subquery(latest.values("created_at")[:1]), has_unopened=Exists(unopened))
    if section == "faves":
        return list(candidates.filter(pk__in=faved_ids).order_by(
            "-has_unopened", "-latest_at", "pk").values_list("pk", flat=True))

    # Sampling is per distinct couple, independent of upload volume or popularity.
    seed = f"{user.pk}:{as_of.date().isoformat()}"
    candidates = candidates.annotate(sample_key=MD5(Concat(Value(seed + ":"), Cast("pk", CharField()))))
    interests = UserInterestModel.objects.filter(removed=False).select_related("interest")
    candidates = list(candidates.order_by("sample_key", "pk").prefetch_related(
        "memberships__user",
        Prefetch("memberships__user__user_interests", queryset=interests, to_attr="discovery_interests"),
    )[:settings.COUPLE_FEED_CANDIDATE_LIMIT])
    own_interests = interest_set([
        item.interest.name if item.interest_id else item.name for item in interests.filter(user=user)])
    if not own_interests:
        membership = CoupleMembershipModel.objects.select_related("couple").filter(user=user).first()
        own_interests = interest_set(membership.couple.shared_interests) if membership else set()
    ranked = []
    for couple in candidates:
        members = [membership.user for membership in couple.memberships.all()]
        tags = interest_set(couple.shared_interests)
        if not tags:
            tags = interest_set([name for member in members for name in interest_names(member)])
        score = rank_score(own_interests, tags, (user.city, user.country),
                           [(member.city, member.country) for member in members],
                           couple.pk in faved_ids, couple.latest_at, as_of,
                           getattr(settings, "COUPLE_FEED_WEIGHTS", DEFAULT_WEIGHTS))
        ranked.append((score, couple.latest_at, couple.pk))
    ranked.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
    return compose([item[2] for item in ranked], faved_ids, seed,
                   settings.COUPLE_FEED_EXPLORATION_INTERVAL)
