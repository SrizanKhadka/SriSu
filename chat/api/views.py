from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework import status, permissions
from chat.api.serializers import CoupleConnectionSerializer
from chat.models import CoupleConnectionModel
from utils.choices import CoupleConnectionStatus
from django.db.models import Q


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

    def is_already_engaged(self, sender_number):
        """
        Checks if the sender is already engaged.
        """
        try:
            # Check if there's an accepted connection in either direction
            connection = CoupleConnectionModel.objects.get(
                Q(
                    sender_number=sender_number,
                    connection_status=CoupleConnectionStatus.ACCEPTED,
                )
                | Q(
                    receiver_number=sender_number,
                    connection_status=CoupleConnectionStatus.ACCEPTED,
                )
            )
            # If such a connection exists, respond accordingly
            if connection:
                return Response(
                    {"message": "You are already engaged."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except CoupleConnectionModel.DoesNotExist:
            return None
        except CoupleConnectionModel.MultipleObjectsReturned:
            return Response(
                {
                    "error": "Multiple active connections found. Data inconsistency detected."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def validate_request_data(self, request):
        sender_number = request.data.get("sender_number")
        receiver_number = request.data.get("receiver_number")
        if not sender_number or not receiver_number:
            raise ValueError("Both sender_number and receiver_number are required.")
        return sender_number, receiver_number

    def create(self, request, *args, **kwargs):
        try:
            sender_number, receiver_number = self.validate_request_data(request)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        connection = self.get_connection(sender_number, receiver_number)

        engaged_response = self.is_already_engaged(sender_number)
        if engaged_response:
            return engaged_response

        if not connection or connection.connection_status in [
            CoupleConnectionStatus.REJECTED,
            CoupleConnectionStatus.BREAKUP,
        ]:
            connection, created = CoupleConnectionModel.objects.update_or_create(
                sender_number=sender_number,
                receiver_number=receiver_number,
                defaults={"connection_status": CoupleConnectionStatus.PENDING},
            )

            return Response(
                {
                    "message": "Connection request sent.",
                    "data": self.serializer_class(connection).data,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        return Response(
            {
                "message": "Connection already exists.",
                "data": self.serializer_class(connection).data,
            },
            status=status.HTTP_200_OK,
        )

    def update(self, request, *args, **kwargs):
        try:
            sender_number, receiver_number = self.validate_request_data(request)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        connection_status = request.data.get("connection_status")
        connection = self.get_connection(sender_number, receiver_number)

        if not connection:
            return Response(
                {"error": "Connection does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if connection_status not in CoupleConnectionStatus.values:
            return Response(
                {"error": "Invalid connection status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if connection_status in [
            CoupleConnectionStatus.ACCEPTED,
            CoupleConnectionStatus.REJECTED,
            CoupleConnectionStatus.BREAKUP,
        ]:
            connection.connection_status = connection_status
            connection.save()
            message = (
                "Connection accepted."
                if connection_status == CoupleConnectionStatus.ACCEPTED
                else "Connection rejected."
            )
            return Response(
                {
                    "message": message,
                    "data": self.serializer_class(connection).data,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": "Unsupported operation."},
            status=status.HTTP_400_BAD_REQUEST,
        )
