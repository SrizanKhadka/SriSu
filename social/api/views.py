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
    GenderChoices,
    SingleConnectionStatus,
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
from social.services.suggestion_service import UserSuggestionService
from social.api.serializers import UserSuggestionSerializer


def sync_chat_room(*, user_one, user_two, chat_type, couple=None, singles=None):
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
            "singles": singles,
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

    if chat_room.singles_id != getattr(singles, "id", None):
        chat_room.singles = singles
        updates.append("singles")

    if updates:
        updates.append("updated_at")
        chat_room.save(update_fields=updates)

    return chat_room


class CoupleConnectionView(ModelViewSet):
    serializer_class = CoupleConnectionSerializer
    queryset = CoupleConnectionModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_connection(self, sender_number, receiver_number):
        """
        Retrieves a connection between two numbers, regardless of direction.
        """
        try:
            return CoupleConnectionModel.objects.get(
                Q(sender_number=sender_number, receiver_number=receiver_number)
                | Q(sender_number=receiver_number, receiver_number=sender_number)
            )
        except CoupleConnectionModel.DoesNotExist:
            return None

    def is_already_engaged(self, number):
        return CoupleConnectionModel.objects.filter(
            Q(sender_number=number, connection_status=CoupleConnectionStatus.ACCEPTED)
            | Q(
                receiver_number=number,
                connection_status=CoupleConnectionStatus.ACCEPTED,
            )
        ).exists()

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

        connection = self.get_connection(sender_number, receiver_number)

        is_sender_engaged = self.is_already_engaged(number=sender_number)
        is_receiver_engaged = self.is_already_engaged(number=receiver_number)

        if is_sender_engaged:
            raise ValidationError({"message": "You are already engaged!"})

        if is_receiver_engaged:
            raise ValidationError({"message": "Requested Person is already engaged!"})

        if not connection or connection.connection_status in [
            CoupleConnectionStatus.REJECTED,
            CoupleConnectionStatus.BREAKUP,
            CoupleConnectionStatus.NOTHING,  # NOTHING is used to cancel the request
        ]:
            connection, created = CoupleConnectionModel.objects.update_or_create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                defaults={"connection_status": CoupleConnectionStatus.PENDING},
            )

            return Response(
                {
                    "message": "Love request sent.",
                    "data": self.get_serializer(connection).data,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        return Response(
            {
                "message": "Love request already exists.",
                "data": self.get_serializer(connection).data,
            },
            status=status.HTTP_200_OK,
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
        connection = self.get_connection(
            sender_number=sender_number, receiver_number=receiver_number
        )

        if not connection:
            return Response(
                {"message": "Connection does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

            # sender user can make it accept or reject but can cancel (nothing) the connection
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

        if connection_status in [
            CoupleConnectionStatus.ACCEPTED,
            CoupleConnectionStatus.REJECTED,
            CoupleConnectionStatus.BREAKUP,
            CoupleConnectionStatus.NOTHING,  # NOTHING is used to cancel the request
        ]:
            with transaction.atomic():
                connection.connection_status = connection_status
                connection.save(update_fields=["connection_status", "updated_at"])

                if (
                    connection
                    and connection.connection_status == CoupleConnectionStatus.REJECTED
                ):
                    message = "Sorry! Love request rejected"
                    return Response(
                        {
                            "message": message,
                            "couple_connection": self.get_serializer(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                elif (
                    connection
                    and connection.connection_status == CoupleConnectionStatus.BREAKUP
                ):
                    message = "Sorry For your break-up. But no worries, You can have better choices."
                    return Response(
                        {
                            "message": message,
                            "couple_connection": self.get_serializer(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                elif (
                    connection
                    and connection.connection_status == CoupleConnectionStatus.NOTHING
                ):
                    message = "Love request cancelled."
                    return Response(
                        {
                            "message": message,
                            "couple_connection": self.get_serializer(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )

                if connection and connection_status == CoupleConnectionStatus.ACCEPTED:
                    try:
                        couple = self.createCouple(couple_connection=connection)
                        UserModel.objects.filter(phone_number=connection.sender_number).update(is_engaged=True)
                        UserModel.objects.filter(phone_number=connection.receiver_number).update(is_engaged=True)
                        
                    except ValueError as exc:
                        connection.connection_status = CoupleConnectionStatus.PENDING
                        connection.save(
                            update_fields=["connection_status", "updated_at"]
                        )
                        return Response(
                            {"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    if couple:
                        sync_chat_room(
                            user_one=couple.male_partner,
                            user_two=couple.female_partner,
                            chat_type=ChatTypeChoices.COUPLE,
                            couple=couple,
                        )
                        return Response(
                            {
                                "message": "Love request accepted!",
                                "couple_connection": self.get_serializer(
                                    connection
                                ).data,
                                "couple": CoupleModelSerializer(couple).data,
                            },
                            status=status.HTTP_200_OK,
                        )
                    else:
                        connection.connection_status = CoupleConnectionStatus.PENDING
                        connection.save(
                            update_fields=["connection_status", "updated_at"]
                        )
                        return Response(
                            {"error": "Couple connection failed."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

        return Response(
            {"message": "Unsupported operation."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def createCouple(self, couple_connection):
        couple_connection_model = couple_connection
        sender_number = couple_connection.sender_number
        receiver_number = couple_connection.receiver_number

        try:
            # Fetch the male partner
            male_partner = UserModel.objects.get(
                Q(phone_number=sender_number, gender=GenderChoices.MALE)
                | Q(phone_number=receiver_number, gender=GenderChoices.MALE)
            )

            # Fetch the female partner
            female_partner = UserModel.objects.get(
                Q(phone_number=sender_number, gender=GenderChoices.FEMALE)
                | Q(phone_number=receiver_number, gender=GenderChoices.FEMALE)
            )

            couple, created = CoupleModel.objects.update_or_create(
                couple_connection_model=couple_connection_model,
                male_partner=male_partner,
                female_partner=female_partner,
            )

            return couple

        except UserModel.DoesNotExist as e:
            raise ValueError(f"User not found: {str(e)}")
        except UserModel.MultipleObjectsReturned as e:
            raise ValueError(f"Data inconsistency detected: {str(e)}")
        except Exception as e:
            raise ValueError(f"Unexpected error occurred: {str(e)}")


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
            connection_status=CoupleConnectionStatus.PENDING,
        )
        .order_by("-created_at")
        .first()
    )

    connection_data = (
        CoupleConnectionSerializer(connection, context={"request": request}).data
        if connection
        else None
    )

    return Response(
        {
            "message": "Connection request check completed.",
            "data": {
                "connection_requested": connection is not None,
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


class CoupleConnectionRequestView(ModelViewSet):
    serializer_class = CoupleConnectionSerializer
    queryset = CoupleConnectionModel.objects.all().order_by("-id")
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CoupleConnectionPagination

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

class CoupleMomentView(ModelViewSet):
    serializer_class = CoupleMomentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def get_queryset(self):
        user = self.request.user
        
        return CoupleMomentModel.objects.filter(
            models.Q(couple__male_partner=user) | 
            models.Q(couple__female_partner=user)
        ).prefetch_related("photos").order_by("-moment_date")
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        photos = request.FILES.getlist("photos")
        
        if len(photos) > 5:
            return Response(
                {"message": "You can upload a maximum of 5 photos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        moment = serializer.save(created_by=request.user)
        
        for index, photo in enumerate(photos):
            CoupleMomentPhotoModel.objects.create(
                moment=moment,
                image=photo,
                order=index,
            )
        
        response_serializer = self.get_serializer(moment)
        return Response(
            {
                "message": "Couple moment created successfully.",
                "data": response_serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )
    
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        
        photos = request.FILES.getlist("photos")
        replace_photos = request.data.get("replace_photos", "false").lower() == "true"
        
        if photos and len(photos) > 5:
            return Response(
                {"message": "You can upload a maximum of 5 photos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        serializer = self.get_serializer(
            instance
            , data=request.data, partial=partial)
        
        serializer.is_valid(raise_exception=True)
        
        moment = serializer.save()
        
        if replace_photos:
            instance.photos.all().delete()
            
            if len(photos) > 5:
                return Response(
                    {"message": "You can upload a maximum of 5 photos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            for index, photo in enumerate(photos):
                CoupleMomentPhotoModel.objects.create(
                    moment=moment,
                    image=photo,
                    order=index,
                )
        elif photos:
            existing_photo_count = instance.photos.count()
            
            if existing_photo_count + len(photos) > 5:
                return Response(
                    {"message": "You can upload a maximum of 5 photos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            for index, photo in enumerate(photos,start=existing_photo_count):
                CoupleMomentPhotoModel.objects.create(
                    moment=moment,
                    image=photo,
                    order=index,
                )
        
        response_serializer = self.get_serializer(moment)
        
        return Response(
            {
                "message": "Couple moment updated successfully.",
                "data": response_serializer.data,
            },
            status=status.HTTP_200_OK,
        )
        
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)
    
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()

        return Response(
            {"message": "Couple moment deleted successfully."},
            status=status.HTTP_200_OK,
        )


class SingleConnectionView(ModelViewSet):
    serializer_class = SingleConnectionSerializer
    queryset = SingleConnectionModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_connection(self, sender_number, receiver_number):
        """
        Retrieves a connection between two numbers, regardless of direction.
        """
        try:
            return SingleConnectionModel.objects.get(
                Q(sender_number=sender_number, receiver_number=receiver_number)
                | Q(sender_number=receiver_number, receiver_number=sender_number)
            )
        except SingleConnectionModel.DoesNotExist:
            return None

    def create(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        sender_number = request.data["sender_number"]
        receiver_number = request.data["receiver_number"]

        if not is_user_valid(
            user_number=request.user.phone_number, sender_number=sender_number
        ):
            return Response(
                {"message": "You don't have permission to perform this operation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        connection = self.get_connection(sender_number, receiver_number)

        if not connection or connection.connection_status in [
            SingleConnectionStatus.REJECTED,
            SingleConnectionStatus.NOTHING,
        ]:
            connection, created = SingleConnectionModel.objects.update_or_create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                defaults={"connection_status": SingleConnectionStatus.PENDING},
            )

            return Response(
                {
                    "message": "Crush request sent.",
                    "data": self.serializer_class(connection).data,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        return Response(
            {
                "message": "Crush request already exists.",
                "data": self.serializer_class(connection).data,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def perform_create(self, serializer):
        return serializer.save()

    def update(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        sender_number = request.data["sender_number"]
        receiver_number = request.data["receiver_number"]
        current_user_number = request.user.phone_number

        if not has_permission(
            request.user.phone_number, sender_number, receiver_number
        ):
            return Response(
                {"message": "You don't have permission to perform this operation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        connection_status = request.data.get("connection_status")
        connection = self.get_connection(
            sender_number=sender_number, receiver_number=receiver_number
        )

        if not connection:
            return Response(
                {"error": "Connection does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # sender user can make it accept or reject but can cancel (nothing) the connection
        if current_user_number == connection.sender_number and connection_status in [
            SingleConnectionStatus.ACCEPTED,
            SingleConnectionStatus.REJECTED,
        ]:
            return Response(
                {"message": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            connection
            and connection.connection_status == SingleConnectionStatus.ACCEPTED
        ) and connection_status == SingleConnectionStatus.REJECTED:
            return Response(
                {"error": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if connection_status in [
            SingleConnectionStatus.ACCEPTED,
            SingleConnectionStatus.REJECTED,
            SingleConnectionStatus.NOTHING,  # NOTHING is used to cancel the request
        ]:
            with transaction.atomic():
                connection.connection_status = connection_status
                connection.save(update_fields=["connection_status", "updated_at"])

                if connection.connection_status == SingleConnectionStatus.REJECTED:
                    message = "Sorry! Crush request rejected."
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                elif connection.connection_status == SingleConnectionStatus.NOTHING:
                    message = "Crush request cancelled."
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )

                elif connection.connection_status == SingleConnectionStatus.ACCEPTED:
                    message = "Crush request accepted!"
                    sync_chat_room(
                        user_one=UserModel.objects.get(phone_number=sender_number),
                        user_two=UserModel.objects.get(phone_number=receiver_number),
                        chat_type=ChatTypeChoices.SINGLE,
                        singles=connection,
                    )
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )

        return Response(
            {"message": "Unsupported operation."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class SingleConnectionRequestView(ModelViewSet):

    serializer_class = SingleConnectionSerializer
    queryset = SingleConnectionModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = PageNumberPagination

    @action(detail=False, methods=["GET"], url_path="sent-requests")
    def retrieve_single_connection_sent_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        sent_requests = SingleConnectionModel.objects.filter(
            sender_number=phone_number, connection_status=SingleConnectionStatus.PENDING
        )
        page_size = request.query_params.get("page_size", 10)
        self.pagination_class.page_size = int(page_size)

        page = self.paginate_queryset(sent_requests)

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
                    "message": "Requests Sent fetched successfully.",
                }
            )

        serializer = self.get_serializer(sent_requests, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["GET"], url_path="received-requests")
    def retrieve_single_connection_request_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        received_requests = SingleConnectionModel.objects.filter(
            receiver_number=phone_number,
            connection_status=SingleConnectionStatus.PENDING,
        )

        page_size = request.query_params.get("page_size", 10)
        self.pagination_class.page_size = int(page_size)

        page = self.paginate_queryset(received_requests)

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
                    "message": "Request received fetched successfully.",
                }
            )

        serializer = self.get_serializer(received_requests, many=True)
        return Response(serializer.data)


class CoupleAPIView(ModelViewSet):
    serializer_class = CoupleModelSerializer
    queryset = CoupleModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def update(self, request, *args, **kwargs):
        instance = self.get_object()

        # Handle photo updates explicitly in the view
        photo_album_data = request.FILES.getlist("couple_photo_album")
        self.upload_photos(photo_album_data=photo_album_data, instance=instance)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": "Couple updated Successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def upload_photos(self, photo_album_data, instance):
        if photo_album_data:
            instance.couple_photo_album.all().delete()
            for photo in photo_album_data:
                PhotoAlbumModel.objects.create(couple=instance, photo=photo)


class UserPreferenceView(ModelViewSet):
    queryset = UserPreferenceModel.objects.all()
    serializer_class = UserPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

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


class UserSuggestionPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class UserSuggestionView(ListAPIView):
    serializer_class = UserSuggestionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UserSuggestionPagination

    def get_queryset(self):
        return UserSuggestionService(user=self.request.user).get_ranked_user_list()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)

        return Response(
            {
                "data": {
                    "count": self.paginator.page.paginator.count,
                    "next": self.paginator.get_next_link(),
                    "previous": self.paginator.get_previous_link(),
                    "results": serializer.data,
                },
                "message": "User suggestions fetched successfully.",
            }
        )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def get_suggestion_profile_by_id(request):
    try:
        user_id = request.query_params.get("user_id")
        user = UserModel.objects.get(id=user_id)
        serializer = UserSuggestionSerializer(user, context={"request": request})
        return Response(
            {
                "message": "Suggestion profile fetched successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )
    except UserModel.DoesNotExist:
        return Response(
            {"message": "User not found."},
            status=status.HTTP_404_NOT_FOUND,
        )


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
