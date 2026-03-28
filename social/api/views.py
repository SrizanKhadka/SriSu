from .serializers import *
from rest_framework import status
from rest_framework.response import Response
import random
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ModelViewSet
from datetime import date
from collections import defaultdict
from django.db.models import OuterRef, Exists
from social.models import *
from authentication.models import UserInterestModel
from utils.choices import CoupleConnectionStatus, GenderChoices
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action, api_view, permission_classes
from authentication.api.serializers import UserModelSerializer
from chat.utils.chatutils import *
from chat.models import ChatRoom
from django.utils import timezone
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
        
        if not is_user_valid(user_number=request.user.phone_number, sender_number=sender_number):
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
            raise ValidationError(
                {"message": "Requested Person is already engaged!"}
            )

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
                    "data": self.serializer_class(connection).data,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        return Response(
            {
                "message": "Love request already exists.",
                "data": self.serializer_class(connection).data,
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
            connection.connection_status = connection_status
            connection.save()

            if (
                connection
                and connection.connection_status == CoupleConnectionStatus.REJECTED
            ):
                message = "Sorry! Love request rejected"
                return Response(
                    {
                        "message": message,
                        "couple_connection": self.serializer_class(connection).data,
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
                        "couple_connection": self.serializer_class(connection).data,
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
                        "couple_connection": self.serializer_class(connection).data,
                    },
                    status=status.HTTP_200_OK,
                )

            if connection and connection_status == CoupleConnectionStatus.ACCEPTED:

                couple = self.createCouple(couple_connection=connection)

                if couple:
                    return Response(
                        {
                            "message": "Love request accepted!",
                            "couple_connection": self.serializer_class(connection).data,
                            "couple": CoupleModelSerializer(couple).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                    
                    #updating the chat rooms for both users
                    ChatRoom.objects.create(
                        user_one=couple.male_partner,
                        user_two=couple.female_partner,
                        chat_type=ChatTypeChoices.COUPLE,
                        couple=couple,
                        updated_at=timezone.now(),
                    )
                    
                    
                else:
                    connection.connection_status = CoupleConnectionStatus.PENDING
                    connection.save()
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

        print("SENDER NUMBER = ", sender_number)
        print("RECEIVER_NUMBER = ", receiver_number)

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


class CoupleConnectionRequestView(ModelViewSet):

    serializer_class = CoupleConnectionSerializer
    queryset = CoupleConnectionModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = PageNumberPagination

    @action(detail=False, methods=["GET"], url_path="sent-requests")
    def retrieve_coupleConnection_sent_list(self, request, *args, **kwargs): #this will provide all the list of sent requests

        phone_number = request.user.phone_number
        sent_requests = CoupleConnectionModel.objects.filter(
            sender_number=phone_number, connection_status=CoupleConnectionStatus.PENDING
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
                "message": "Love Requests Sent fetched successfully.",
            } 
         )

        serializer = self.get_serializer(sent_requests, many=True)
        return Response(serializer.data)

    @action(
        detail=False, methods=["GET"], url_path="received-requests"
    )
    def retrieve_couple_connection_request_list(self, request, *args, **kwargs): #this will provide all the list of received requests

        phone_number = request.user.phone_number
        received_requests = CoupleConnectionModel.objects.filter(
            receiver_number=phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        page = self.paginate_queryset(received_requests)
        page_size = request.query_params.get("page_size", 10)
        self.pagination_class.page_size = int(page_size)
        
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
                "message": "Love Request received fetched successfully.",
            }
            )

        serializer = self.get_serializer(received_requests, many=True)
        return Response(serializer.data)


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
        
        if not is_user_valid(user_number=request.user.phone_number, sender_number=sender_number):
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
            CoupleConnectionStatus.ACCEPTED,
            CoupleConnectionStatus.REJECTED,
        ]:
            return Response(
                {"message": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if ( connection and connection.connection_status == SingleConnectionStatus.ACCEPTED) and connection_status == SingleConnectionStatus.REJECTED:
            return Response(
                {"error": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if connection_status in [
            SingleConnectionStatus.ACCEPTED,
            SingleConnectionStatus.REJECTED,
            SingleConnectionStatus.NOTHING,  # NOTHING is used to cancel the request
        ]:
            connection.connection_status = connection_status
            connection.save()

            if connection:
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
                    ChatRoom.objects.create(
                        user_one=UserModel.objects.get(phone_number=sender_number),
                        user_two=UserModel.objects.get(phone_number=receiver_number),
                        chat_type=ChatTypeChoices.SINGLE,
                        singles=connection,
                        updated_at=timezone.now(),
                    )
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
            else:
                connection.connection_status = CoupleConnectionStatus.PENDING
                connection.save()
                return Response(
                    {"message": "Crush connection failed."},
                    status=status.HTTP_400_BAD_REQUEST,
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

    @action(
        detail=False, methods=["GET"], url_path="received-requests"
    )
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
            {
                "message": "User preference returned successfully.",
                "data": data
            },
            status=status.HTTP_200_OK
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


class UserSuggestionView(ModelViewSet):
    queryset = UserModel.objects.all()
    serializer_class = UserSuggestionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UserSuggestionPagination
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        user = self.request.user
        preferences = UserPreferenceModel.objects.filter(user=user).first()
        gender = (
            GenderChoices.MALE
            if user.gender == GenderChoices.FEMALE
            else GenderChoices.FEMALE
        ) 

        connection_sent = SingleConnectionModel.objects.filter(
            Q(sender_number=user.phone_number, receiver_number=OuterRef("phone_number")) |
            Q(receiver_number=user.phone_number, sender_number=OuterRef("phone_number"))
        ).exclude(
            connection_status__in=[SingleConnectionStatus.NOTHING, SingleConnectionStatus.REJECTED]
        )

        if not preferences:
            all_users = UserModel.objects.exclude(id=user.id).filter(gender=gender)
            all_users = list(all_users.annotate(crushed=Exists(connection_sent)))

            random.seed(request.user.id)
            random.shuffle(all_users)

            page = self.paginate_queryset(all_users)
            serializer = self.get_serializer(page, many=True)
            return Response(
                {
                    "data": {
                        "count": self.paginator.page.paginator.count,
                        "next": self.paginator.get_next_link(),
                        "previous": self.paginator.get_previous_link(),
                        "results": serializer.data,
                    },
                    "message": "User Suggestions fetched successfully.",
                }
            )

        today = date.today()
        min_birth_year = today.year - preferences.max_age
        max_birth_year = today.year - preferences.min_age
        zodiac_sign = preferences.zodiac_sign

        filtered_users = UserModel.objects.exclude(id=user.id)

        if preferences.city:
            filtered_users = filtered_users.filter(city=preferences.city)
        elif preferences.country:
            filtered_users = filtered_users.filter(country=preferences.country)

        if gender:
            filtered_users = filtered_users.filter(gender=gender)

        if zodiac_sign:
            filtered_users = filtered_users.filter(zodiac_sign=zodiac_sign)

        if min_birth_year and max_birth_year:
            filtered_users = filtered_users.filter(
                dob__year__range=(min_birth_year, max_birth_year)
            )
            
        filtered_users = filtered_users.annotate(crushed=Exists(connection_sent))

        all_interest_qs = UserInterestModel.objects.filter(user__in=filtered_users)

        # Build a dictionary {user_id: set of interests}
        user_interest_map = defaultdict(set)
        for obj in all_interest_qs:
            user_interest_map[obj.user_id].add(obj.name)

        user_interests = set(
            UserInterestModel.objects.filter(user=user).values_list("name", flat=True)
        )

        scored_users = []
        for candidate in filtered_users:
            candidate_interests = user_interest_map.get(candidate.id, set())
            intersection = user_interests & candidate_interests

            score = (
                int((len(intersection) / len(user_interests)) * 100)
                if user_interests
                else 0
            )
            scored_users.append((candidate, score))

        strong = [user for user, score in scored_users if score >= 70]
        medium = [user for user, score in scored_users if 40 <= score < 70]
        weak = [user for user, score in scored_users if score < 40]

        sorted_users = strong + medium + weak

        page = self.paginate_queryset(sorted_users)
        serializer = self.get_serializer(page, many=True)
        return Response(
            {
                "data": {
                    "count": self.paginator.page.paginator.count,
                    "next": self.paginator.get_next_link(),
                    "previous": self.paginator.get_previous_link(),
                    "results": serializer.data,
                },
                "message": "User Suggestions fetched successfully.",
            }
        )
        
@api_view(['GET'])
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
            serializer = UserModelSerializer(partner, context={'request': request})
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