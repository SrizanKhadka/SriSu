from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.api.moment_serializers import CoupleMomentSerializer
from social.services.moment_publication import create_draft, draft_for, upload_photo, publish
from social.services.moment_service import hydrate_moments, eligible_moments


class DraftInput(StrictSerializer):
    request_id = serializers.UUIDField()
    couple = serializers.IntegerField(min_value=1)


class PublishInput(StrictSerializer):
    expected_revision = serializers.IntegerField(min_value=1)
    caption = serializers.CharField(max_length=1000, allow_blank=True, default="")
    visibility = serializers.ChoiceField(choices=["private", "public"], default="private")
    photo_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=5)


class DraftThrottle(UserRateThrottle):
    scope = "moment_drafts"
    rate = "60/hour"
    def allow_request(self, request, view):
        return request.method in ("GET", "HEAD", "OPTIONS") or super().allow_request(request, view)


def draft_data(request, draft):
    return {"id": str(draft.pk), "couple": draft.couple_id, "revision": draft.revision,
        "created_at": draft.created_at, "published_at": draft.published_at,
        "moment_id": draft.moment_id, "server_time": timezone.now(),
        "photos": [{"id": str(p.pk), "width": p.width, "height": p.height,
            "image": request.build_absolute_uri(f"/api/social/moment-drafts/{draft.pk}/photos/{p.pk}/")}
            for p in draft.photos.order_by("created_at", "id")]}


class MomentDraftView(PrivateResponseMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    throttle_classes = [DraftThrottle]
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def create(self, request):
        data = DraftInput(data=request.data)
        data.is_valid(raise_exception=True)
        draft, created = create_draft(
            request.user, data.validated_data["request_id"], data.validated_data["couple"])
        return Response({"data": draft_data(request, draft)}, status=201 if created else 200)

    def retrieve(self, request, pk=None):
        return Response({"data": draft_data(request, draft_for(request.user, pk))})

    def destroy(self, request, pk=None):
        with transaction.atomic():
            draft = draft_for(request.user, pk, lock=True, editable=True)
            draft.delete()
        return Response(status=204)

    @action(detail=True, methods=["get", "put", "delete"], url_path=r"photos/(?P<photo_id>[0-9a-fA-F-]{36})")
    def photo(self, request, pk=None, photo_id=None):
        # Validate UUID shape/content before ORM lookup (the URL regex is intentionally bounded).
        field = serializers.UUIDField()
        pk, photo_id = field.run_validation(pk), field.run_validation(photo_id)
        if request.method in ("GET", "HEAD"):
            from social.api.couple_profile_views import photo_response
            draft = draft_for(request.user, pk)
            photo = draft.photos.filter(pk=photo_id).first()
            if photo is None:
                raise Http404
            return photo_response(photo.image)
        if request.method == "PUT":
            if set(request.data) != {"photo"}:
                raise serializers.ValidationError({"photo": "Provide one image."})
            draft = upload_photo(request.user, pk, photo_id, request.FILES.get("photo"))
        else:
            with transaction.atomic():
                draft = draft_for(request.user, pk, lock=True, editable=True)
                deleted, _ = draft.photos.filter(pk=photo_id).delete()
                if deleted:
                    draft.revision += 1
                    draft.save(update_fields=["revision"])
        return Response({"data": draft_data(request, draft)})

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        data = PublishInput(data=request.data)
        data.is_valid(raise_exception=True)
        moment, created = publish(request.user, pk, data.validated_data)
        moment = hydrate_moments(eligible_moments(request.user).filter(pk=moment.pk), request.user).first()
        if moment is None:
            raise Http404
        return Response({"data": CoupleMomentSerializer(moment, context={"request": request}).data},
            status=201 if created else 200)
