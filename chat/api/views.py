from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework import status, permissions
from chat.api.serializers import *
from chat.models import *
from utils.choices import CoupleConnectionStatus, GenderChoices
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from chat.utils.chatutils import *


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

        connection = self.get_connection(sender_number, receiver_number)

        is_sender_engaged = self.is_already_engaged(number=sender_number)
        is_receiver_engaged = self.is_already_engaged(number=receiver_number)

        if is_sender_engaged:
            raise serializers.ValidationError({"message": "You are already engaged!"})

        if is_receiver_engaged:
            raise serializers.ValidationError(
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
        if sender_number == connection.sender_number and connection_status in [
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
    pagination_class.page_size = 10

    @action(detail=False, methods=["GET"], url_path="sent-requests")
    def retrieve_coupleConnection_sent_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        sent_requests = CoupleConnectionModel.objects.filter(
            sender_number=phone_number, connection_status=CoupleConnectionStatus.PENDING
        )
        page = self.paginate_queryset(sent_requests)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(sent_requests, many=True)
        return Response(serializer.data)

    @action(
        detail=False, methods=["GET"], url_path="received-requests"
    )
    def retrieve_couple_connection_request_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        received_requests = CoupleConnectionModel.objects.filter(
            receiver_number=phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        page = self.paginate_queryset(received_requests)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

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

        connection = self.get_connection(sender_number, receiver_number)

        if not connection or connection.connection_status in [
            SingleConnectionStaus.REJECTED,
            SingleConnectionStaus.NOTHING,
        ]:
            connection, created = SingleConnectionModel.objects.update_or_create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                defaults={"connection_status": SingleConnectionStaus.PENDING},
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
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        return serializer.save()

    def update(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        sender_number = request.data["sender_number"]
        receiver_number = request.data["receiver_number"]
        
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
        if sender_number == connection.sender_number and connection_status in [
            CoupleConnectionStatus.ACCEPTED,
            CoupleConnectionStatus.REJECTED,
        ]:
            return Response(
                {"message": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            connection
            and connection.connection_status == SingleConnectionStaus.ACCEPTED
        ) and connection_status == SingleConnectionStaus.REJECTED:
            return Response(
                {"error": "Unsupported operation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if connection_status in [
            SingleConnectionStaus.ACCEPTED,
            SingleConnectionStaus.REJECTED,
            SingleConnectionStaus.NOTHING,  # NOTHING is used to cancel the request
        ]:
            connection.connection_status = connection_status
            connection.save()

            if connection:
                if connection.connection_status == SingleConnectionStaus.REJECTED:
                    message = "Sorry! Love request rejected."
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                elif connection.connection_status == SingleConnectionStaus.NOTHING:
                    message = "Love request cancelled."
                    return Response(
                        {
                            "message": message,
                            "single_connection": self.serializer_class(connection).data,
                        },
                        status=status.HTTP_200_OK,
                    )
                
                elif connection.connection_status == SingleConnectionStaus.ACCEPTED:
                    message = "Crush request accepted!"
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
    pagination_class.page_size = 10

    @action(detail=False, methods=["GET"], url_path="sent-requests")
    def retrieve_single_connection_sent_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        sent_requests = SingleConnectionModel.objects.filter(
            sender_number=phone_number, connection_status=SingleConnectionStaus.PENDING
        )
        page = self.paginate_queryset(sent_requests)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(sent_requests, many=True)
        return Response(serializer.data)

    @action(
        detail=False, methods=["GET"], url_path="received-requests"
    )
    def retrieve_single_connection_request_list(self, request, *args, **kwargs):

        phone_number = request.user.phone_number
        received_requests = SingleConnectionModel.objects.filter(
            receiver_number=phone_number,
            connection_status=SingleConnectionStaus.PENDING,
        )
        page = self.paginate_queryset(received_requests)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

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
        print("PHOTO ALBUM DATA", photo_album_data)
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


class MediaUploadView(ModelViewSet):
    serializer_class = MediaModelSerializer
    queryset = MediaModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
