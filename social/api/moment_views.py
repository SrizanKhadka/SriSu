from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.viewsets import ModelViewSet, GenericViewSet

from social.api.moment_serializers import CoupleMomentSerializer, MomentNoteSerializer, MomentNoteReplySerializer
from social.models import (CoupleMomentModel, CoupleMomentNoteModel, CoupleMomentViewModel,
    CoupleMembershipModel, MomentReaction)
from social.services.moment_service import (visible_moments, save_moment, compensate_upload,
    may_modify, membership_snapshot, lock_couple, visible_notes)
from social.services.moment_service import eligible_moments, active_couples, blocked_user_ids
from django.db.models import Exists, OuterRef, Count, Q, Min
from django.utils import timezone


class CoupleMomentPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({"data": {"count": self.page.paginator.count,
            "next": self.get_next_link(), "previous": self.get_previous_link(), "results": data},
            "message": "Results fetched successfully."})


class MomentNoteThrottle(UserRateThrottle):
    scope = "moment_notes"
    rate = "30/hour"


class MomentReplyThrottle(MomentNoteThrottle):
    scope = "moment_note_replies"

    def allow_request(self, request, view):
        return request.method != "POST" or super().allow_request(request, view)


class PrivateResponseMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class CoupleMomentView(PrivateResponseMixin, ModelViewSet):
    serializer_class = CoupleMomentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    pagination_class = CoupleMomentPagination
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        queryset = visible_moments(self.request.user)
        couple = self.request.query_params.get("couple")
        if couple is not None:
            if not couple.isdecimal() or len(couple) > 18 or int(couple) < 1:
                raise ValidationError({"couple": "Must be a positive integer ID."})
            queryset = queryset.filter(couple_id=int(couple))
        return queryset

    def get_object(self):
        if len(str(self.kwargs["pk"])) > 18:
            raise Http404
        try:
            return super().get_object()
        except Http404:
            if self.request.query_params.get("record_view") == "false" and eligible_moments(
                    self.request.user, include_expired=True).filter(pk=self.kwargs["pk"], expires_at__lte=timezone.now()).exists():
                from social.services.moment_publication import MomentExpired
                raise MomentExpired()
            raise

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.method == "GET" and request.query_params.get("record_view") != "false":
            # Serialize with deletion and recheck expiry after waiting for the lock.
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            obj = self.get_object()
            CoupleMomentViewModel.objects.get_or_create(moment=obj, viewer=request.user)
            # Rehydrate the bounded viewer projection and aggregate count after insertion.
            obj = self.get_object()
        return Response(self.get_serializer(obj).data)

    @action(detail=False, methods=["get"])
    def home(self, request):
        user = request.user
        now = timezone.now()
        own = CoupleMembershipModel.objects.filter(user=user, ended_at__isnull=True).values("couple_id")
        blocked = CoupleMembershipModel.objects.filter(user_id__in=blocked_user_ids(user), ended_at__isnull=True).values("couple_id")
        couple = active_couples().filter(pk__in=own).exclude(pk__in=blocked).first()
        result = {"server_time": now, "user": {"id": user.pk, "name": user.full_name or user.username or ""}, "couple": None}
        if couple:
            viewed = CoupleMomentViewModel.objects.filter(moment_id=OuterRef("pk"), viewer=user)
            summaries = {s["created_by_id"]: s for s in eligible_moments(user).filter(couple=couple)
                .annotate(opened=Exists(viewed)).values("created_by_id").annotate(
                    active_count=Count("pk"), unseen_count=Count("pk", filter=Q(opened=False)),
                    next_expiry=Min("expires_at"))}
            members = []
            for m in couple.memberships.filter(ended_at__isnull=True).select_related("user").order_by("position"):
                summary = summaries.get(m.user_id, {})
                members.append({"id": m.user_id, "name": m.user.full_name or m.user.username or "",
                    "photo_url": request.build_absolute_uri(f"/api/social/profiles/{couple.pk}/members/{m.user_id}/photo/") if m.user.profile_photo else None,
                    "active_count": summary.get("active_count", 0), "unseen_count": 0 if m.user_id == user.pk else summary.get("unseen_count", 0),
                    "next_expiry": summary.get("next_expiry")})
            result["couple"] = {"id": couple.pk, "anniversary_date": couple.anniversary_date, "members": members}
        return Response({"data": result})

    @action(detail=True, methods=["post"])
    def viewed(self, request, pk=None):
        if request.data:
            raise ValidationError("No body is accepted.")
        with transaction.atomic():
            obj = self.get_object()
            lock_couple(obj.couple_id)
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            obj = self.get_object()
            if request.user.pk != obj.created_by_id:
                CoupleMomentViewModel.objects.get_or_create(moment=obj, viewer=request.user)
        return Response({"data": self.get_serializer(self.get_object()).data})

    @action(detail=True, methods=["put"])
    def reaction(self, request, pk=None):
        if set(request.data) != {"reacted"} or type(request.data["reacted"]) is not bool:
            raise ValidationError({"reacted": "Provide a boolean."})
        with transaction.atomic():
            obj = self.get_object()
            lock_couple(obj.couple_id)
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            obj = self.get_object()
            if request.user.pk not in obj.audience_user_ids or request.user.pk == obj.created_by_id:
                raise PermissionDenied()
            if request.data["reacted"]:
                MomentReaction.objects.get_or_create(moment=obj, user=request.user)
            else:
                MomentReaction.objects.filter(moment=obj, user=request.user).delete()
        return Response({"data": self.get_serializer(self.get_object()).data})

    @action(detail=True, methods=["get"], url_path="creator-photo")
    def creator_photo(self, request, pk=None):
        from social.api.couple_profile_views import photo_response
        return photo_response(self.get_object().created_by.profile_photo)

    @action(detail=True, methods=["patch"])
    def caption(self, request, pk=None):
        from rest_framework import serializers
        from social.api.couple_profile_serializers import StrictSerializer
        from social.services.moment_publication import MomentConflict
        class Input(StrictSerializer):
            caption = serializers.CharField(max_length=1000, allow_blank=True)
            expected_revision = serializers.IntegerField(min_value=1)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = self.get_object()
            lock_couple(obj.couple_id)
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            obj = self.get_object()
            if not may_modify(obj, request.user):
                raise PermissionDenied()
            if obj.revision != data.validated_data["expected_revision"]:
                raise MomentConflict()
            if not data.validated_data["caption"].strip() and not obj.photos.exists():
                raise ValidationError({"caption": "A caption or photo is required."})
            obj.caption = data.validated_data["caption"]
            obj.revision += 1
            obj.save(update_fields=["caption", "revision", "updated_at"])
        return Response({"data": self.get_serializer(self.get_object()).data})

    def _write(self, request, partial=False, updating=False):
        written = []
        try:
            initial = self.get_object() if updating else None
            if initial and not may_modify(initial, request.user):
                raise PermissionDenied("Only the creator can modify this moment.")
            context = self.get_serializer_context()
            context["written_files"] = written
            serializer = self.get_serializer(initial, data=request.data, partial=partial, context=context)
            serializer.is_valid(raise_exception=True)
            # Decode/encode and storage writes finish before publication starts.
            import uuid
            from social.models import CoupleMomentPhotoModel
            prepared = []
            storage = CoupleMomentPhotoModel._meta.get_field("image").storage
            for upload in serializer.validated_data.get("photos", []):
                name = f"couples/moments/{uuid.uuid4().hex}.jpg"
                written.append(name)
                saved = storage.save(name, upload)
                if saved != name:
                    written.append(saved)
                prepared.append((saved, upload.moment_dimensions))
            context["prepared_uploads"] = prepared
            with transaction.atomic():
                moment = None
                if updating:
                    # Acquire before reading photos/validating the combined limit.
                    obj = self.get_object()
                    lock_couple(obj.couple_id)
                    get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
                    moment = self.get_object()  # recheck expiry after waiting for the lock
                    if not may_modify(moment, request.user):
                        raise PermissionDenied("Only the creator can modify this moment.")
                moment = save_moment(serializer, request.user, moment)
                data = self.get_serializer(moment).data
        except Exception:
            for name in written:
                compensate_upload(name)
            raise
        return Response({"message": "Couple moment saved successfully.", "data": data},
                        status=200 if updating else 201)

    def create(self, request, *args, **kwargs):
        return self._write(request)

    def update(self, request, *args, **kwargs):
        return self._write(request, partial=kwargs.pop("partial", False), updating=True)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        lock_couple(obj.couple_id)
        get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
        obj = self.get_object()
        if not may_modify(obj, request.user):
            raise PermissionDenied("Only the creator can delete this moment.")
        obj.delete()
        return Response({"message": "Couple moment deleted successfully."})

    @action(detail=True, methods=["get"], url_path=r"photos/(?P<photo_id>[0-9]+)")
    def photo(self, request, photo_id=None, **kwargs):
        moment = self.get_object()
        if len(photo_id) > 18:
            raise Http404
        photo = get_object_or_404(moment.photos, pk=photo_id)
        try:
            response = FileResponse(photo.image.open("rb"), content_type="application/octet-stream")
        except FileNotFoundError:
            raise Http404
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["post"], throttle_classes=[MomentNoteThrottle])
    def notes(self, request, **kwargs):
        with transaction.atomic():
            obj = self.get_object()
            lock_couple(obj.couple_id)
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            moment = self.get_object()
            recipients = membership_snapshot(moment.couple)
            if moment.couple.memberships.filter(user=request.user, ended_at__isnull=True).exists():
                raise ValidationError("You cannot send appreciation to your own couple.")
            serializer = MomentNoteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            note = serializer.save(moment=moment, sender=request.user, recipient_membership_ids=recipients,
                recipient_user_ids=list(moment.couple.memberships.filter(ended_at__isnull=True).order_by("id").values_list("user_id", flat=True)))
        return Response({"data": MomentNoteSerializer(note).data}, status=status.HTTP_201_CREATED)


class MomentNoteView(PrivateResponseMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                     mixins.DestroyModelMixin, GenericViewSet):
    serializer_class = MomentNoteSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CoupleMomentPagination
    lookup_value_regex = "[0-9]{1,18}"
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = visible_notes(self.request.user)
        moment = self.request.query_params.get("moment")
        if moment is not None:
            if not moment.isdecimal() or len(moment) > 18 or int(moment) < 1:
                raise ValidationError({"moment": "Must be a positive integer ID."})
            queryset = queryset.filter(moment_id=int(moment))
        return queryset

    @action(detail=True, methods=["get", "post"], throttle_classes=[MomentReplyThrottle])
    def replies(self, request, **kwargs):
        if request.method in ("GET", "HEAD"):
            note = self.get_object()
            page = self.paginate_queryset(note.replies.all())
            return self.get_paginated_response(MomentNoteReplySerializer(page, many=True).data)
        with transaction.atomic():
            note = self.get_object()
            lock_couple(note.moment.couple_id)
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=note.moment_id)
            get_object_or_404(CoupleMomentNoteModel.objects.select_for_update(), pk=note.pk)
            note = self.get_object()
            if not visible_notes(request.user, recipients_only=True).filter(pk=note.pk).exists():
                raise PermissionDenied("Only the original current couple members can reply to this note.")
            serializer = MomentNoteReplySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            reply = serializer.save(note=note, author=request.user)
        return Response({"data": MomentNoteReplySerializer(reply).data}, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        if instance.sender_id != self.request.user.id:
            raise PermissionDenied("Only the sender can delete this note.")
        instance.delete()
