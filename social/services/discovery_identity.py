"""Bulk, consent-bound identity projection; never serialize member-only profiles."""
from django.db.models import Prefetch
from authentication.models import UserInterestModel
from social.models import CoupleModel, CoupleMembershipModel
from social.services.couple_profile_sections import audience, digest
from social.services.couple_scorer import normalize


def identity_values(members):
    return [{"id": m.user_id, "name": m.user.full_name or m.user.username or "",
             "photo": m.user.profile_photo.name} for m in members]


def approved_section(couple, members, section, raw):
    proposal = couple.privacy_proposal or {}
    approved = {c.user_id for c in couple.privacy_consents.all()
                if c.proposal_version == couple.privacy_proposal_version}
    return (len(members) == 2 and proposal.get("audience") == audience(members)
            and approved == {m.user_id for m in members}
            and section in proposal.get("sections", [])
            and proposal.get("fingerprints", {}).get(section) == digest(raw))


def approved_identity(couple, members):
    """Approval is bound to current exact identity and membership audience."""
    return approved_section(couple, members, "identity", identity_values(members))


def public_values(ids):
    """Bulk public projection; internal file names are converted to authorized URLs later."""
    interests = UserInterestModel.objects.filter(removed=False).only("user_id", "name")
    members = CoupleMembershipModel.objects.select_related("user").order_by("position").prefetch_related(
        Prefetch("user__user_interests", queryset=interests, to_attr="public_discovery_interests"))
    rows = CoupleModel.objects.filter(pk__in=ids).prefetch_related(
        Prefetch("memberships", queryset=members), "privacy_consents")
    result = {}
    for couple in rows:
        people = list(couple.memberships.all())
        value = {"id": couple.pk}
        raw = identity_values(people)
        if approved_section(couple, people, "identity", raw):
            value["members"] = raw
            value["display_name"] = " & ".join(p["name"] for p in raw if p["name"])
        interests = {str(m.user_id): sorted({i.name for i in m.user.public_discovery_interests}, key=str.casefold)
                     for m in people}
        if approved_section(couple, people, "interests", interests):
            sets = [set(values) for values in interests.values()]
            value["shared_interests"] = sorted(set.intersection(*sets), key=str.casefold) if sets else []
        result[couple.pk] = value
    return result


def matches_public(value, query="", interest_label=None):
    """Only already-consented public values participate, never captions/private names."""
    shared = {normalize(label) for label in value.get("shared_interests", [])}
    if interest_label is not None and normalize(interest_label) not in shared:
        return False
    haystack = normalize(" ".join([value.get("display_name", ""), *value.get("shared_interests", [])]))
    return all(token in haystack for token in query.split())


def identities(request, ids):
    result = public_values(ids)
    for couple_id, value in result.items():
        if "members" in value:
            value["members"] = [{"id": person["id"], "name": person["name"], "photo_url":
                request.build_absolute_uri(f'/api/social/profiles/{couple_id}/members/{person["id"]}/photo/')
                if person["photo"] else None} for person in value["members"]]
            value["avatars"] = [person["photo_url"] for person in value["members"] if person["photo_url"]]
    return result
