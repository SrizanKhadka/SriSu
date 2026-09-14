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
from social.models import CoupleMomentModel, CoupleMomentNoteModel, CoupleMomentViewModel
from social.services.moment_service import (visible_moments, save_moment, compensate_upload,
    may_modify, membership_snapshot, lock_couple, visible_notes)


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
        return super().get_object()

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.method == "GET":
            # Serialize with deletion and recheck expiry after waiting for the lock.
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            obj = self.get_object()
            CoupleMomentViewModel.objects.get_or_create(moment=obj, viewer=request.user)
            obj.total_view_count = obj.views.count()
        return Response(self.get_serializer(obj).data)

    def _write(self, request, partial=False, updating=False):
        written = []
        try:
            with transaction.atomic():
                moment = None
                if updating:
                    # Acquire before reading photos/validating the combined limit.
                    obj = self.get_object()
                    get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
                    moment = self.get_object()  # recheck expiry after waiting for the lock
                    if not may_modify(moment, request.user):
                        raise PermissionDenied("Only the creator can modify this moment.")
                context = self.get_serializer_context()
                context["written_files"] = written
                serializer = self.get_serializer(moment, data=request.data, partial=partial, context=context)
                serializer.is_valid(raise_exception=True)
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
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=obj.pk)
            lock_couple(obj.couple_id)
            moment = self.get_object()
            recipients = membership_snapshot(moment.couple)
            if moment.couple.memberships.filter(user=request.user).exists():
                raise ValidationError("You cannot send appreciation to your own couple.")
            serializer = MomentNoteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            note = serializer.save(moment=moment, sender=request.user, recipient_membership_ids=recipients,
                recipient_user_ids=list(moment.couple.memberships.order_by("id").values_list("user_id", flat=True)))
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
            get_object_or_404(CoupleMomentModel.objects.select_for_update(), pk=note.moment_id)
            lock_couple(note.moment.couple_id)
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
