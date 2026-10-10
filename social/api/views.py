import json

from .serializers import *
from rest_framework import status
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from django.db import transaction
from social.models import *
from utils.choices import (
    CoupleConnectionStatus,
    ChatTypeChoices,
)
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action, api_view, permission_classes
from authentication.api.serializers import UserModelSerializer
from chat.utils.chatutils import *
from chat.models import ChatRoom
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from social.services.couple_profile_service import (
    CoupleProfileConflict,
    create_or_get_couple_for_connection,
)


def sync_chat_room(*, user_one, user_two, chat_type, couple=None):
    """
    Keep one chat room per user pair and attach the latest social relation to it.
    """
    ordered_users = sorted([user_one, user_two], key=lambda user: user.id)
    chat_room, _ = ChatRoom.objects.get_or_create(
        user_one=ordered_users[0],
        user_two=ordered_users[1],
        defaults={
            "chat_type": chat_type,
            "couple": couple,
        },
    )

    updates = []

    if chat_room.user_one_id != ordered_users[0].id:
        chat_room.user_one = ordered_users[0]
        updates.append("user_one")

    if chat_room.user_two_id != ordered_users[1].id:
        chat_room.user_two = ordered_users[1]
        updates.append("user_two")

    if chat_room.chat_type != chat_type:
        chat_room.chat_type = chat_type
        updates.append("chat_type")

    if chat_room.couple_id != getattr(couple, "id", None):
        chat_room.couple = couple
        updates.append("couple")

    if updates:
        updates.append("updated_at")
        chat_room.save(update_fields=updates)

    return chat_room


class CoupleConnectionView(ModelViewSet):
    serializer_class = CoupleConnectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        number = self.request.user.phone_number
        return CoupleConnectionModel.objects.filter(Q(sender_number=number) | Q(receiver_number=number))

    def create(self, request, *args, **kwargs):
        from social.services.relationship_service import invite
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["sender_number"] != request.user.phone_number:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        connection, created = invite(request.user, data["receiver_number"])
        return Response({"message": "Love request sent." if created else "Love request already exists.",
                         "data": self.get_serializer(connection).data}, status=201 if created else 200)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        from couple_chat.services import describe
        from social.services.relationship_service import transition
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection, couple, room = transition(request.user, kwargs["pk"],
            request.data.get("connection_status"), data["sender_number"], data["receiver_number"])
        payload = self.get_serializer(connection).data
        response = {"message": "Connection updated.", "data": payload, "couple_connection": payload}
        if couple:
            # Existing released-client adapter remains independent of the new app.
            members = list(couple.members.order_by("id"))
            sync_chat_room(user_one=members[0], user_two=members[1], chat_type=ChatTypeChoices.COUPLE, couple=couple)
            response.update(message="Love request accepted!", couple=CoupleModelSerializer(couple, context=self.get_serializer_context()).data,
                            couple_chat=describe(room))
            response["data"] = {**payload, "couple_chat": describe(room)}
        return Response(response)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def have_couple_connection_requested(request):
    sender_number = getattr(request.user, "phone_number", None)

    if not sender_number:
        return Response(
            {"error": "Phone number is missing."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    connection = (
        CoupleConnectionModel.objects.filter(
            sender_number=sender_number,
            connection_status__in=[CoupleConnectionStatus.PENDING, CoupleConnectionStatus.ACCEPTED],
        )
        .order_by("-created_at")
        .first()
    )

    connection_data = (
        CoupleConnectionSerializer(connection, context={"request": request}).data
        if connection
        else None
    )
    if connection and connection.connection_status == CoupleConnectionStatus.ACCEPTED:
        from couple_chat.services import available_rooms, describe
        room = available_rooms(request.user).filter(couple__couple_connection=connection).first()
        if room:
            connection_data["couple_chat"] = describe(room)

    return Response(
        {
            "message": "Connection request check completed.",
            "data": {
                "connection_requested": connection is not None and connection.connection_status == CoupleConnectionStatus.PENDING,
                "connection": connection_data
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


class CoupleConnectionRequestView(ReadOnlyModelViewSet):
    serializer_class = CoupleConnectionSerializer
    queryset = CoupleConnectionModel.objects.all().order_by("-id")
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CoupleConnectionPagination

    def get_queryset(self):
        number = self.request.user.phone_number
        return super().get_queryset().filter(Q(sender_number=number) | Q(receiver_number=number))

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
        partner = UserModel.objects.get(phone_number=phone_number)

        if not partner:
            return Response(
                {"message": "Partner not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
            serializer = UserModelSerializer(partner, context={"request": request})
            return Response(
                {
                    "message": "Partner found successfully.",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )
    except Exception as e:
        return Response(
            {"message": "Partner not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
