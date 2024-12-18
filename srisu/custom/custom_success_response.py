from rest_framework.response import Response
from rest_framework import status


def custom_success_response(message, data=None):

    response_data = data

    if not data or data is None:
        response_data = message

    return Response(
        {"message": message, "data": response_data}, status=status.HTTP_200_OK
    )
