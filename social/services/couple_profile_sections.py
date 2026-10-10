"""Viewer projections and narrow, revision-checked Couple Profile operations.

Never reuse the legacy serializer for a visitor. Consent binds content AND the
current membership IDs, so replacement/relinking cannot inherit publication.
"""
import hashlib
import json
from datetime import date

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from authentication.models import UserInterestModel, InterestModel
from social.models import (CoupleModel, CoupleMembershipModel, CoupleConnectionModel,
    CoupleStoryAnswerModel, CoupleSongModel, CoupleProfileChangeModel,
    CouplePrivacyConsentModel, CoupleFaveModel)
from social.services.moment_service import active_couples, blocked_user_ids, eligible_moments

SECTIONS = ("identity", "story", "song", "interests", "cover", "date")
EDITABLE = ("story", "song", "interests", "cover", "date", "sharing")
PROMPTS = dict(CoupleStoryAnswerModel.PROMPTS)


class ProfileConflict(APIException):
    status_code = 409
    default_detail = "This section changed. Reload it before saving. Your draft has not been saved."
    default_code = "section_changed"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def profile_for(user, couple_id=None, *, owner=False, lock=False):
    if couple_id is None:
        couple_id = CoupleMembershipModel.objects.filter(user=user).values_list("couple_id", flat=True).first()
    if not couple_id:
        raise Http404
    if lock:
        # Same lock order as ordinary profile writes. Membership/connection deletion
        # cannot pass these row locks during a conditional mutation.
        candidate = get_object_or_404(CoupleModel.objects.select_for_update(), pk=couple_id)
        list(CoupleConnectionModel.objects.select_for_update().filter(pk=candidate.couple_connection_id))
        list(CoupleMembershipModel.objects.select_for_update().filter(couple_id=couple_id).order_by("id"))
    blocked = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user)).values("couple_id")
    couple = get_object_or_404(active_couples().exclude(pk__in=blocked), pk=couple_id)
    members = list(couple.memberships.select_related("user").order_by("position"))
    is_member = any(m.user_id == user.pk for m in members)
    if owner and not is_member:
        raise Http404
    return couple, members, is_member


def interests_for(user):
    return sorted(set(UserInterestModel.objects.filter(user=user, removed=False).values_list("name", flat=True)), key=str.casefold)


def raw_sections(couple, members, user):
    ids = [m.user_id for m in members]
    interests = {str(m.user_id): interests_for(m.user) for m in members}
    answers = list(couple.story_answers.filter(author_id__in=ids).order_by("prompt", "author_id").values("author_id", "prompt", "answer"))
    song = CoupleSongModel.objects.filter(couple=couple).values("title", "artist", "band", "note", "picked_by_id").first()
    source = couple.cover_source_moment_photo
    # A Moment cover is a reference, never a copy that extends the Moment lifetime.
    valid_source = source is not None and eligible_moments(user).filter(pk=source.moment_id, couple=couple).exists()
    cover = {"name": couple.cover_photo.name if not source else "", "source": source.pk if valid_source else None,
             "focal_y": couple.cover_focal_y}
    if source and not valid_source:
        cover["name"] = ""
    return {
        "identity": [{"id": m.user_id, "name": m.user.full_name or m.user.username or "", "photo": m.user.profile_photo.name} for m in members],
        "story": answers, "song": song, "interests": interests, "cover": cover,
        "date": couple.anniversary_date.isoformat() if couple.anniversary_date else None,
    }


def audience(members):
    return sorted(m.pk for m in members)


def revisions(couple, raw, user):
    values = dict(raw)
    values["story"] = [a for a in raw["story"] if a["author_id"] == user.pk]
    values["interests"] = raw["interests"].get(str(user.pk), [])
    values["sharing"] = {"proposal": couple.privacy_proposal, "version": couple.privacy_proposal_version}
    return {key: digest({"value": value, "revision": couple.section_revisions.get(f"{key}:{user.pk}" if key in ("story", "interests") else key, 0)}) for key, value in values.items()}


def published_sections(couple, members, raw):
    proposal = couple.privacy_proposal or {}
    if proposal.get("audience") != audience(members):
        return []
    approved = set(couple.privacy_consents.filter(proposal_version=couple.privacy_proposal_version).values_list("user_id", flat=True))
    if approved != {m.user_id for m in members}:
        return []
    return [section for section in proposal.get("sections", []) if section in SECTIONS and proposal.get("fingerprints", {}).get(section) == digest(raw[section])]


def profile_data(request, couple, members, is_member):
    raw = raw_sections(couple, members, request.user)
    published = published_sections(couple, members, raw)
    allowed = set(SECTIONS if is_member else published)
    base = f"/api/social/profiles/{couple.pk}"
    def url(path):
        return request.build_absolute_uri(base + path)
    data = {"id": couple.pk, "viewer": "member" if is_member else "visitor", "can_edit": is_member,
        "published_sections": published, "is_faved": CoupleFaveModel.objects.filter(user=request.user, couple=couple).exists(),
        "can_fave": not is_member and settings.COUPLE_FEED_ENABLED,
        "can_view_moments": settings.COUPLE_FEED_ENABLED,
        "visible_moment_count": eligible_moments(request.user).filter(couple=couple).count()}
    if "identity" in allowed:
        data["members"] = [{"id": m["id"], "name": m["name"], "photo_url": url(f"/members/{m['id']}/photo/") if m["photo"] else None} for m in raw["identity"]]
    if "story" in allowed:
        data["story"] = raw["story"]
    if "song" in allowed:
        data["song"] = raw["song"]
    if "interests" in allowed:
        sets = [set(x) for x in raw["interests"].values()]
        shared = sorted(set.intersection(*sets), key=str.casefold) if sets else []
        data["interests"] = {"shared": shared}
        if is_member:
            data["interests"].update(mine=raw["interests"][str(request.user.pk)], partner=next(v for k,v in raw["interests"].items() if k != str(request.user.pk)))
    if "cover" in allowed:
        cover = raw["cover"]
        data["cover"] = {"url": url("/cover/") if cover["name"] or cover["source"] else None, "focal_y": cover["focal_y"]}
    if "date" in allowed:
        data["anniversary_date"] = raw["date"]
        start = couple.anniversary_date or timezone.localdate(couple.created_at)
        data["days_together"] = max(0, (timezone.localdate() - start).days)
        today = timezone.localdate()
        if couple.anniversary_date:
            def anniversary(year):
                try:
                    return start.replace(year=year)
                except ValueError:
                    return date(year, 2, 28)
            upcoming = anniversary(today.year)
            if upcoming < today:
                upcoming = anniversary(today.year + 1)
            data["days_to_anniversary"] = (upcoming - today).days
    if is_member:
        data["revisions"] = revisions(couple, raw, request.user)
        data["sharing"] = {"proposal": (couple.privacy_proposal or {}).get("sections", []),
            "proposal_version": couple.privacy_proposal_version,
            "approved_by": list(couple.privacy_consents.filter(proposal_version=couple.privacy_proposal_version).values_list("user_id", flat=True)),
            "valid_proposal": (couple.privacy_proposal or {}).get("audience") == audience(members) and all((couple.privacy_proposal or {}).get("fingerprints", {}).get(s) == digest(raw[s]) for s in (couple.privacy_proposal or {}).get("sections", []))}
        plans = couple.plans.filter(audience_membership_ids=audience(members))
        data["plan_count"] = plans.count()
        data["plans_done"] = plans.filter(completed=True).count()
        data["plans_upcoming"] = plans.filter(starts_at__gte=timezone.now()).count()
    return data


def ensure_revision(couple, members, user, section, expected):
    current = revisions(couple, raw_sections(couple, members, user), user)[section]
    if expected != current:
        raise ProfileConflict()


def record_change(couple, user, section, action="updated"):
    key = f"{section}:{user.pk}" if section in ("story", "interests") else section
    couple.section_revisions = {**couple.section_revisions, key: couple.section_revisions.get(key, 0) + 1}
    couple.revision += 1
    couple.save(update_fields=["section_revisions", "revision", "updated_at"])
    CoupleProfileChangeModel.objects.create(couple=couple, actor=user, section=section, action=action)


def update_section(couple, members, user, section, data):
    if not (section == "sharing" and data.get("action") == "revoke"):
        ensure_revision(couple, members, user, section, data["expected_revision"])
    if section == "story":
        # Missing prompts are untouched; explicit empty strings remove only self.
        for prompt, answer in data["answers"].items():
            if answer:
                CoupleStoryAnswerModel.objects.update_or_create(couple=couple, author=user, prompt=prompt, defaults={"answer": answer})
            else:
                CoupleStoryAnswerModel.objects.filter(couple=couple, author=user, prompt=prompt).delete()
    elif section == "song":
        if data.get("remove"):
            CoupleSongModel.objects.filter(couple=couple).delete()
        else:
            CoupleSongModel.objects.update_or_create(couple=couple, defaults={k: data[k] for k in ("title", "artist", "band", "note")} | {"picked_by": user})
    elif section == "date":
        couple.anniversary_date = data["anniversary_date"]
        couple.save(update_fields=["anniversary_date"])
    elif section == "interests":
        names = data["names"]
        existing = list(UserInterestModel.objects.filter(user=user))
        catalogue = {item.name.casefold(): item for item in InterestModel.objects.all()}
        kept = set()
        for name in names:
            row = next((item for item in existing if item.name.casefold() == name.casefold() and item.pk not in kept), None)
            if row is None:
                row = UserInterestModel(user=user)
            row.name = name
            row.removed = False
            if row.interest_id is None:
                row.interest = catalogue.get(name.casefold())
            row.save()
            kept.add(row.pk)
        UserInterestModel.objects.filter(user=user).exclude(pk__in=kept).update(removed=True)
    elif section == "sharing":
        raw = raw_sections(couple, members, user)
        action = data["action"]
        if action == "approve":
            proposal = couple.privacy_proposal or {}
            if proposal.get("audience") != audience(members) or any(proposal.get("fingerprints", {}).get(s) != digest(raw[s]) for s in proposal.get("sections", [])):
                raise ProfileConflict()
            CouplePrivacyConsentModel.objects.update_or_create(couple=couple, user=user, defaults={"proposal_version": couple.privacy_proposal_version})
        else:
            # Revocation cancels the entire proposal immediately. A fresh proposal
            # may select the remaining sections; no unilateral widening of access.
            sections = [] if action == "revoke" else data["sections"]
            if "cover" in sections and couple.cover_source_moment_photo_id and couple.cover_source_moment_photo.moment.visibility != "public":
                raise ValidationError({"sections": "A private Moment cannot be published as a cover. Choose an uploaded cover or an already-public Moment."})
            couple.privacy_proposal_version += 1
            couple.privacy_proposal = {"sections": sections, "audience": audience(members), "fingerprints": {s: digest(raw[s]) for s in sections}}
            couple.save(update_fields=["privacy_proposal", "privacy_proposal_version"])
            couple.privacy_consents.all().delete()
            CouplePrivacyConsentModel.objects.create(couple=couple, user=user, proposal_version=couple.privacy_proposal_version)
    record_change(couple, user, section)
