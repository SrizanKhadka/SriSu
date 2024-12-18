from django.http import JsonResponse
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework import exceptions

def customExceptionHandler(exception, context):
    response = drf_exception_handler(exception, context)

    # print(response, response.data)

    if response is not None and isinstance(exception, exceptions.APIException):

        # Extract error messages and format them into a single string
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

            # response.data['message'] = error_messages[0]

            response.data = {
                "error_details": response.data,
                "message": error_messages[0]
            }

        return response

    return response
