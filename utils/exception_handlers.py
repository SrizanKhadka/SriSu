from django.http import JsonResponse
from rest_framework.views import exception_handler
from rest_framework import exceptions


def custom_exception_handler(exception, context):
    
    response = exception_handler(exception, context)

    # print(response, response.data)

    if response is not None and isinstance(exception, exceptions.APIException):

        if isinstance(response.data, dict):
            error_messages = []
            for key, value in response.data.items():
                if isinstance(value, list):
                    for error in value:
                        if key == 'non_field_errors':
                            error_messages.append(f"{str(error)}")
                        else:
                            error_messages.append(f"{key}: {str(error)}")
                else:
                    error_messages.append(f"{key}: {str(value)}")

            response.data = {
                "error_details": response.data,
                "message": error_messages[0] if error_messages[0] else "An unknown error occured"
            }

        return response

    return response
