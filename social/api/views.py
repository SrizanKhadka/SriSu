import json
from uuid import UUID

from .serializers import *
from rest_framework import status
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ModelViewSet
from django.db import transaction
from social.models import *
from utils.choices import (
    CoupleConnectionStatus,
)
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.throttling import UserRateThrottle
from social.services.phone_identity import has_permission, is_user_valid
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from social.services.relationship_service import (
    RelationshipConflict,
    RelationshipPermissionDenied,
    accept_connection,
    create_connection_request,
    end_connection,
)


class PartnerDiscoveryThrottle(UserRateThrottle):
    scope = "partner_discovery"


class CoupleConnectionView(ModelViewSet):
    serializer_class = CoupleConnectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "put", "head", "options"]

    def get_queryset(self):
        """Conceal relationship records from every non-participant."""
        phone_number = getattr(self.request.user, "phone_number", None)
        if not phone_number:
            return CoupleConnectionModel.objects.none()
        return CoupleConnectionModel.objects.filter(
            Q(sender_number=phone_number) | Q(receiver_number=phone_number)
        ).order_by("-updated_at", "-id")

    def _connection_data(self, connection, *, couple=None, room=None, replayed=False):
        return {
            **self.get_serializer(connection).data,
            "chat_room_id": str(room.pk) if room else None,
            "couple_id": couple.pk if couple else None,
            "replayed": replayed,
        }

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        sender_number = data["sender_number"]
        receiver_number = data["receiver_number"]

        if not is_user_valid(
            user_number=request.user.phone_number, sender_number=sender_number
        ):
            return Response(
                {"message": "You don't have permission to perform this operation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        operation_id = request.headers.get("Idempotency-Key")
        try:
            operation_id = UUID(operation_id) if operation_id else None
            result = create_connection_request(
                sender_number=sender_number,
                receiver_number=receiver_number,
                actor=request.user,
                operation_id=operation_id,
            )
        except RelationshipPermissionDenied:
            return Response(
                {"message": "You don't have permission to perform this operation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except RelationshipConflict as exc:
            return Response(
                {"message": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except ValueError as exc:
            raise ValidationError({"message": str(exc)}) from exc

        return Response(
            {
                "message": "Love request already exists." if result.replayed else "Love request sent.",
                "data": self._connection_data(
                    result.connection,
                    replayed=result.replayed,
                ),
            },
            status=status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        return serializer.save()

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sender_number = data["sender_number"]
        receiver_number = data["receiver_number"]
        current_user_number = request.user.phone_number
        if not has_permission(
            request.user.phone_number, sender_number, receiver_number
        ):
            return Response(
                {"message": "You don't have permission to perform this operation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        connection_status = request.data.get("connection_status")
        connection = self.get_queryset().filter(pk=kwargs.get("pk")).first()
        if not connection:
            return Response(
                {"message": "Connection does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if {connection.sender_number, connection.receiver_number} != {
            sender_number,
            receiver_number,
        }:
            return Response(
                {"message": "Connection does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # The sender may cancel, but cannot accept or reject their own request.
        if current_user_number == connection.sender_number and connection_status in [
            CoupleConnectionStatus.ACCEPTED,
            CoupleConnectionStatus.REJECTED,
        ]:
            return Response(
                {"message": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            connection
            and connection.connection_status == CoupleConnectionStatus.ACCEPTED
        ) and connection_status == CoupleConnectionStatus.REJECTED:
            return Response(
                {"message": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if connection_status == CoupleConnectionStatus.ACCEPTED:
            operation_id = request.headers.get("Idempotency-Key")
            try:
                operation_id = UUID(operation_id) if operation_id else None
                result = accept_connection(
                    connection_id=connection.pk,
                    actor=request.user,
                    operation_id=operation_id,
                )
            except RelationshipPermissionDenied:
                return Response(
                    {"message": "You don't have permission to perform this operation."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            except (RelationshipConflict, ValueError):
                return Response(
                    {"message": "The relationship could not be accepted."},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {
                    "message": "Love request accepted!",
                    "data": self._connection_data(
                        result.connection,
                        couple=result.couple,
                        room=result.chat_room,
                        replayed=result.replayed,
                    ),
                    "couple_connection": self.get_serializer(result.connection).data,
                    "couple": CoupleModelSerializer(result.couple).data,
                    "chat_room_id": str(result.chat_room.pk),
                    "replayed": result.replayed,
                },
                status=status.HTTP_200_OK,
            )

        if connection_status == CoupleConnectionStatus.BREAKUP:
            try:
                connection = end_connection(connection_id=connection.pk, actor=request.user)
            except RelationshipPermissionDenied:
                return Response(
                    {"message": "You don't have permission to perform this operation."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            except RelationshipConflict:
                return Response(
                    {"message": "The relationship could not be ended."},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {
                    "message": "Relationship ended.",
                    "data": self._connection_data(connection),
                    "couple_connection": self.get_serializer(connection).data,
                },
                status=status.HTTP_200_OK,
            )

        if connection_status in [
            CoupleConnectionStatus.REJECTED,
            CoupleConnectionStatus.NOTHING,
        ]:
            if (
                connection_status == CoupleConnectionStatus.REJECTED
                and current_user_number != connection.receiver_number
            ) or (
                connection_status == CoupleConnectionStatus.NOTHING
                and current_user_number != connection.sender_number
            ):
                return Response(
                    {"message": "You don't have permission to perform this operation."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            operation_id = request.headers.get("Idempotency-Key")
            try:
                UUID(operation_id) if operation_id else None
            except ValueError as exc:
                raise ValidationError({"message": "Idempotency-Key must be a UUID."}) from exc
            with transaction.atomic():
                connection = CoupleConnectionModel.objects.select_for_update().get(pk=connection.pk)
                replayed = connection.connection_status == connection_status
                if not replayed and connection.connection_status != CoupleConnectionStatus.PENDING:
                    return Response(
                        {"message": "Unsupported operation."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if not replayed:
                    connection.connection_status = connection_status
                    connection.revision += 1
                    connection.save(update_fields=["connection_status", "revision", "updated_at"])
            return Response(
                {
                    "message": "Love request rejected" if connection_status == CoupleConnectionStatus.REJECTED else "Love request cancelled.",
                    "data": self._connection_data(connection, replayed=replayed),
                    "couple_connection": self.get_serializer(connection).data,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"message": "Unsupported operation."},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def have_couple_connection_requested(request):
    phone_number = getattr(request.user, "phone_number", None)

    if not phone_number:
        return Response(
            {"error": "Phone number is missing."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    membership = (
        CoupleMembershipModel.objects.select_related("couple__couple_connection")
        .filter(
            user=request.user,
            ended_at__isnull=True,
            couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED,
            couple__couple_connection__ended_at__isnull=True,
        )
        .order_by("-joined_at", "-id")
        .first()
    )
    room = None
    if membership is not None:
        connection = membership.couple.couple_connection
        room = membership.couple.chat_rooms.order_by("-updated_at", "-created_at").first()
    else:
        connection = (
            CoupleConnectionModel.objects.filter(
                sender_number=phone_number,
                connection_status=CoupleConnectionStatus.PENDING,
            )
            .order_by("-created_at", "-id")
            .first()
        )

    connection_data = None
    if connection is not None:
        connection_data = CoupleConnectionSerializer(
            connection,
            context={"request": request},
        ).data
        connection_data.update(
            {
                "chat_room_id": str(room.pk) if room else None,
                "couple_id": membership.couple_id if membership else None,
                "replayed": False,
            }
        )

    requested = bool(
        connection
        and connection.connection_status == CoupleConnectionStatus.PENDING
        and connection.sender_number == phone_number
    )

    return Response(
        {
            "message": "Connection request check completed.",
            "data": {
                "connection_requested": requested,
                "connection": connection_data,
            },
        },
        status=status.HTTP_200_OK,
    )

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def is_engaged(request):
    user_number = getattr(request.user, "phone_number", None)

    if not user_number:
        return Response(
            {"error": "Phone number is missing."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    user = UserModel.objects.filter(phone_number=user_number).first()
    is_engaged = user.is_engaged if user else False

    return Response(
        {
            "message": "User Engagement Details",
            "data": {
                "is_engaged": is_engaged
            },
        },
        status=status.HTTP_200_OK,
    )



class CoupleConnectionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class CoupleConnectionRequestView(ModelViewSet):
    serializer_class = CoupleConnectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CoupleConnectionPagination
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        phone_number = getattr(self.request.user, "phone_number", None)
        if not phone_number:
            return CoupleConnectionModel.objects.none()
        return CoupleConnectionModel.objects.filter(
            Q(sender_number=phone_number) | Q(receiver_number=phone_number)
        ).order_by("-id")

    def get_paginated_response_data(self, queryset, message):
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return Response(
                {
                    "data": {
                        "count": self.paginator.page.paginator.count,
                        "next": self.paginator.get_next_link(),
                        "previous": self.paginator.get_previous_link(),
                        "results": serializer.data,
                    },
                    "message": message,
                }
            )

        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "data": {
                    "count": queryset.count(),
                    "next": None,
                    "previous": None,
                    "results": serializer.data,
                },
                "message": message,
            }
        )

    @action(detail=False, methods=["GET"], url_path="sent-requests")
    def retrieve_couple_connection_sent_list(self, request, *args, **kwargs):
        phone_number = request.user.phone_number

        sent_requests = CoupleConnectionModel.objects.filter(
            sender_number=phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        ).order_by("-id")

        return self.get_paginated_response_data(
            sent_requests,
            "Love Requests Sent fetched successfully.",
        )

    @action(detail=False, methods=["GET"], url_path="received-requests")
    def retrieve_couple_connection_request_list(self, request, *args, **kwargs):
        phone_number = request.user.phone_number

        received_requests = CoupleConnectionModel.objects.filter(
            receiver_number=phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        ).order_by("-id")

        return self.get_paginated_response_data(
            received_requests,
            "Love Requests Received fetched successfully.",
        )

from social.api.moment_views import CoupleMomentView, CoupleMomentPagination






from social.api.moment_views import PrivateResponseMixin
from social.services.couple_profile_sections import profile_for
from social.services.moment_service import active_couples, blocked_user_ids


class CoupleProfileAPIView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def get_couple(user):
        from django.http import Http404
        try:
            return profile_for(user, owner=True)[0]
        except Http404:
            return None

    def get(self, request, *args, **kwargs):
        couple = self.get_couple(request.user)
        if not couple:
            return Response(
                {"message": "Couple profile does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CoupleModelSerializer(
            couple,
            context={"request": request},
        )
        return Response(
            {
                "message": "Couple profile retrieved successfully.",
                "data": {"couple_profile": serializer.data},
            },
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        couple = self.get_couple(request.user)
        if couple and couple.profile_completed_at:
            return Response(
                {"message": "Couple profile already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = CoupleModelSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        couple = serializer.save()

        return Response(
            {
                "message": "Couple profile created successfully.",
                "data": {
                    "couple_profile": CoupleModelSerializer(
                        couple,
                        context={"request": request},
                    ).data
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        couple = self.get_couple(request.user)
        if not couple:
            return Response(
                {"message": "Couple profile does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        couple, _, _ = profile_for(request.user, couple.pk, owner=True, lock=True)
        serializer = CoupleModelSerializer(
            couple,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": "Couple profile updated successfully.",
                "data": {"couple_profile": serializer.data},
            },
            status=status.HTTP_200_OK,
        )


class CoupleAPIView(PrivateResponseMixin, ModelViewSet):
    """Compatibility endpoint for clients using the former update-couple route."""

    serializer_class = CoupleModelSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        return (
            active_couples().filter(memberships__user=self.request.user).exclude(memberships__user_id__in=blocked_user_ids(self.request.user))
            .select_related("couple_connection")
            .prefetch_related("memberships__user", "couple_photo_album")
            .distinct()
        )

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        instance, _, _ = profile_for(request.user, self.get_object().pk, owner=True, lock=True)
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        photo_album_data = request.FILES.getlist("couple_photo_album")
        if photo_album_data:
            instance.couple_photo_album.all().delete()
            for photo in photo_album_data:
                PhotoAlbumModel.objects.create(couple=instance, photo=photo)

        serializer.save()
        return Response(
            {
                "message": "Couple updated successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class UserPreferenceView(ModelViewSet):
    queryset = UserPreferenceModel.objects.all()
    serializer_class = UserPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Personal preferences survive dating retirement, but are never directory data.
        return self.queryset.filter(user=self.request.user)

    def validate_user_request(self, data, user):
        user_data = data.get("user")
        if not user_data or user_data.id != user.id:
            raise ValidationError({"error": "User does not match."})

        if not user.is_profile_complete:
            raise ValidationError({"error": "User profile is not complete."})

        if not user.is_phone_verified:
            raise ValidationError({"error": "User phone number is not verified."})

    def get_object(self):
        obj = UserPreferenceModel.objects.filter(user=self.request.user).first()
        if not obj:
            return None
        return obj

    @action(detail=False, methods=["get"], url_path="me")
    def get_my_preference(self, request):
        instance = self.queryset.filter(user=request.user).first()
        data = self.get_serializer(instance).data if instance else None
        return Response(
            {"message": "User preference returned successfully.", "data": data},
            status=status.HTTP_200_OK,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.validate_user_request(data=serializer.validated_data, user=request.user)
        self.perform_create(serializer)

        return Response(
            {
                "message": "User preference created successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        self.validate_user_request(data=serializer.validated_data, user=request.user)

        self.perform_update(serializer)

        return Response(
            {
                "message": "User preference updated successfully.",
                "data": serializer.data,
            }
        )

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()








@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([PartnerDiscoveryThrottle])
def find_partner(request):
    phone_number = request.query_params.get("phone_number")

    if not phone_number:
        return Response(
            {"error": "Phone number is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if phone_number and not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    try:
        partner = UserModel.objects.get(phone_number=phone_number, is_active=True)
    except UserModel.DoesNotExist:
        return Response(
            {"message": "Partner not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    serializer = PartnerDiscoverySerializer(partner, context={"request": request})
    return Response(
        {
            "message": "Partner found successfully.",
            "data": serializer.data,
        },
        status=status.HTTP_200_OK,
    )
