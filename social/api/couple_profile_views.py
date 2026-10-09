from uuid import uuid4
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from social.api.moment_views import PrivateResponseMixin
from social.api.couple_pagination import positive_int
from social.api.couple_profile_serializers import (StoryInput, SongInput, DateInput,
    InterestsInput, SharingInput, CoverInput, PlanInput, PlanResponseInput, StoryInviteInput)
from social.models import (
    CoupleModel,
    CoupleMomentPhotoModel,
    CouplePlanModel,
    CoupleStoryInviteModel,
)
from social.services.couple_profile_sections import (profile_for, profile_data, raw_sections,
    published_sections, update_section, ensure_revision, record_change, audience, ProfileConflict)
from social.services.moment_service import eligible_moments, queue_file_deletion, compensate_upload


class ProfileThrottle(UserRateThrottle):
    rate = "120/min"
    scope = "couple_profile"


class ProfileAPI(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [ProfileThrottle]


def saved(request, couple, members):
    return Response({"message": "Profile saved.", "data": profile_data(request, couple, members, True)})


class ProfileDetail(ProfileAPI):
    def get(self, request, couple_id=None):
        couple, members, member = profile_for(request.user, couple_id)
        return Response({"message": "Profile fetched.", "data": profile_data(request, couple, members, member)})


class ProfileSection(ProfileAPI):
    serializers = {"story": StoryInput, "song": SongInput, "date": DateInput,
                   "interests": InterestsInput, "sharing": SharingInput}
    def patch(self, request, couple_id, section):
        serializer_class = self.serializers.get(section)
        if serializer_class is None:
            raise Http404
        profile_for(request.user, couple_id, owner=True)
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            couple, members, _ = profile_for(request.user, couple_id, owner=True, lock=True)
            update_section(couple, members, request.user, section, serializer.validated_data)
            return saved(request, couple, members)


class ProfileCover(ProfileAPI):
    parser_classes = [JSONParser, MultiPartParser]
    def get(self, request, couple_id):
        couple, members, member = profile_for(request.user, couple_id)
        raw = raw_sections(couple, members, request.user)
        if not member and "cover" not in published_sections(couple, members, raw):
            raise Http404
        source = couple.cover_source_moment_photo
        if source:
            get_object_or_404(eligible_moments(request.user), pk=source.moment_id, couple=couple)
            field = source.image
        else:
            field = couple.cover_photo
        return photo_response(field)

    def patch(self, request, couple_id):
        profile_for(request.user, couple_id, owner=True)
        serializer = CoverInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        storage = CoupleModel._meta.get_field("cover_photo").storage
        uploaded = None
        # Decode/re-encode and storage I/O happen before holding database row locks.
        if data.get("photo"):
            uploaded = storage.save(f"couples/profile_private/{uuid4()}.jpg", data["photo"])
        try:
            with transaction.atomic():
                couple, members, _ = profile_for(request.user, couple_id, owner=True, lock=True)
                ensure_revision(couple, members, request.user, "cover", data["expected_revision"])
                old = couple.cover_photo.name
                replacing = bool(uploaded or data.get("moment_photo_id") or data["remove"])
                if data.get("moment_photo_id"):
                    source = get_object_or_404(CoupleMomentPhotoModel.objects.select_related("moment"), pk=data["moment_photo_id"], moment__couple=couple)
                    get_object_or_404(eligible_moments(request.user), pk=source.moment_id)
                    # Existing audience checks establish that both current members
                    # were recipients. No ownership change or permanent copy.
                    couple.cover_source_moment_photo = source
                    couple.cover_photo = ""
                elif uploaded or data["remove"]:
                    couple.cover_source_moment_photo = None
                    couple.cover_photo = uploaded or ""
                couple.cover_focal_y = data["focal_y"]
                couple.save(update_fields=["cover_photo", "cover_source_moment_photo", "cover_focal_y"])
                record_change(couple, request.user, "cover")
                if replacing and old and old != uploaded:
                    queue_file_deletion(old)
                response = saved(request, couple, members)
            return response
        except Exception:
            if uploaded:
                compensate_upload(uploaded)
            raise


def photo_response(field):
    if not field:
        raise Http404
    try:
        response = FileResponse(field.open("rb"), content_type="application/octet-stream")
    except (FileNotFoundError, OSError):
        raise Http404
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class ProfileMemberPhoto(ProfileAPI):
    def get(self, request, couple_id, user_id):
        couple, members, member = profile_for(request.user, couple_id)
        raw = raw_sections(couple, members, request.user)
        if not member and "identity" not in published_sections(couple, members, raw):
            raise Http404
        match = next((m for m in members if m.user_id == user_id), None)
        if not match:
            raise Http404
        return photo_response(match.user.profile_photo)


class ProfileHistory(ProfileAPI):
    def get(self, request, couple_id):
        couple, members, _ = profile_for(request.user, couple_id, owner=True)
        before = request.query_params.get("before")
        rows = couple.profile_changes.filter(actor_id__in=[m.user_id for m in members])
        if before:
            rows = rows.filter(pk__lt=positive_int(before, "before"))
        rows = list(rows.order_by("-id")[:21])
        return Response({"message": "History fetched.", "data": {"results": [{"id": r.pk, "actor_id": r.actor_id, "section": r.section, "action": r.action, "created_at": r.created_at} for r in rows[:20]], "next_before": rows[19].pk if len(rows) > 20 else None}})


class ProfileCoverChoices(ProfileAPI):
    def get(self, request, couple_id):
        couple, members, _ = profile_for(request.user, couple_id, owner=True)
        photos = CoupleMomentPhotoModel.objects.filter(moment__in=eligible_moments(request.user).filter(couple=couple)).select_related("moment").order_by("-id")
        before = request.query_params.get("before")
        if before:
            photos = photos.filter(pk__lt=positive_int(before, "before"))
        rows = list(photos[:21])
        return Response({"message": "Cover choices fetched.", "data": {"results": [{"id": p.pk, "url": request.build_absolute_uri(f"/api/social/couple-moments/{p.moment_id}/photos/{p.pk}/")} for p in rows[:20]], "next_before": rows[19].pk if len(rows) > 20 else None}})


def plan_data(plan):
    return {"id": plan.pk, "created_by": plan.created_by_id, "title": plan.title, "starts_at": plan.starts_at,
            "response": plan.response, "response_note": plan.response_note, "completed": plan.completed, "revision": plan.revision}


class ProfilePlans(ProfileAPI):
    def get(self, request, couple_id):
        couple, members, _ = profile_for(request.user, couple_id, owner=True)
        rows = couple.plans.filter(audience_membership_ids=audience(members))
        mode = request.query_params.get("mode", "upcoming")
        if mode not in ("upcoming", "past"):
            raise ValidationError({"mode": "Choose upcoming or past."})
        rows = rows.filter(starts_at__gte=timezone.now()) if mode == "upcoming" else rows.filter(starts_at__lt=timezone.now())
        before = request.query_params.get("before")
        if before:
            rows = rows.filter(pk__lt=positive_int(before, "before"))
        rows = list(rows.order_by("-id")[:21])
        return Response({"message": "Plans fetched.", "data": {"results": [plan_data(p) for p in rows[:20]], "next_before": rows[19].pk if len(rows) > 20 else None}})

    def post(self, request, couple_id):
        profile_for(request.user, couple_id, owner=True)
        serializer = PlanInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            couple, members, _ = profile_for(request.user, couple_id, owner=True, lock=True)
            if data["starts_at"] < timezone.now() and not CouplePlanModel.objects.filter(couple=couple, created_by=request.user, request_id=data["request_id"]).exists():
                raise ValidationError({"starts_at": "Choose a future time."})
            plan, created = CouplePlanModel.objects.get_or_create(couple=couple, created_by=request.user, request_id=data["request_id"], defaults={"title": data["title"], "starts_at": data["starts_at"], "audience_membership_ids": audience(members)})
            if not created and (plan.title != data["title"] or plan.starts_at != data["starts_at"] or plan.audience_membership_ids != audience(members)):
                raise ProfileConflict()
            if created:
                record_change(couple, request.user, "plans")
            return Response({"message": "Plan saved.", "data": plan_data(plan)}, status=201 if created else 200)


class ProfilePlanDetail(ProfileAPI):
    def get(self, request, couple_id, plan_id):
        couple, members, _ = profile_for(request.user, couple_id, owner=True)
        plan = get_object_or_404(couple.plans, pk=plan_id, audience_membership_ids=audience(members))
        return Response({"message": "Plan fetched.", "data": plan_data(plan)})

    def patch(self, request, couple_id, plan_id):
        serializer = PlanResponseInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            couple, members, _ = profile_for(request.user, couple_id, owner=True, lock=True)
            plan = get_object_or_404(couple.plans.select_for_update(), pk=plan_id, audience_membership_ids=audience(members))
            if plan.revision != data["expected_revision"]:
                raise ProfileConflict()
            if "response" in data:
                if plan.created_by_id == request.user.pk:
                    raise ValidationError({"response": "Your partner responds to your invitation."})
                plan.response = data["response"]
                plan.response_note = data["response_note"]
            else:
                if plan.starts_at > timezone.now() or plan.response != "yes":
                    raise ValidationError({"completed": "Only an agreed past plan can be marked done."})
                plan.completed = data["completed"]
            plan.revision += 1
            plan.save(update_fields=["response", "response_note", "completed", "revision"])
            record_change(couple, request.user, "plans")
            return Response({"message": "Plan saved.", "data": plan_data(plan)})


class ProfileStoryInvite(ProfileAPI):
    def post(self, request, couple_id):
        serializer = StoryInviteInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            couple, members, _ = profile_for(request.user, couple_id, owner=True, lock=True)
            invite, created = CoupleStoryInviteModel.objects.get_or_create(
                couple=couple,
                created_by=request.user,
                request_id=data["request_id"],
                defaults={
                    "prompt": data["prompt"],
                    "audience_membership_ids": audience(members),
                },
            )
            if not created and (
                invite.prompt != data["prompt"]
                or invite.audience_membership_ids != audience(members)
            ):
                raise ProfileConflict()
            if created:
                record_change(couple, request.user, "story_invites")
            return Response(
                {
                    "message": "Question saved for your shared profile.",
                    "data": {
                        "id": invite.pk,
                        "request_id": str(invite.request_id),
                        "prompt": invite.prompt,
                        "created_by": invite.created_by_id,
                        "created_at": invite.created_at,
                    },
                },
                status=201 if created else 200,
            )
