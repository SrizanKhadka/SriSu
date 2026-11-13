"""Opt-in core errors; preserve the legacy APIException envelope for old clients."""
import logging

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

from srisu.api.errors import CONTRACT, MESSAGES, error_envelope, field_codes

logger = logging.getLogger("srisu.api")


def custom_exception_handler(exception, context):
    response = exception_handler(exception, context)
    request = context.get("request")
    opted_in = request is not None and request.headers.get("X-SriSu-Contract") == CONTRACT
    if response is None:
        if not opted_in:
            return None  # Preserve Django exception handling for existing clients.
        set_rollback()
        # Provider exceptions can contain secrets; do not log exception text/locals.
        logger.error("unhandled_api_error", extra={
            "exception_type": type(exception).__name__,
            "request_id": getattr(request, "request_id", None),
        })
        response = Response({"message": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if opted_in:
        code = getattr(exception, "core_error_code", None)
        codes = exception.get_codes() if isinstance(exception, exceptions.ValidationError) else {}
        response.data = error_envelope(
            response.status_code, code=code if code in MESSAGES else None, request_id=getattr(request, "request_id", None),
            fields=field_codes(codes), retry_after=getattr(exception, "wait", None),
        )
    elif isinstance(exception, exceptions.APIException) and isinstance(response.data, dict):
        messages = []
        for key, value in response.data.items():
            for item in value if isinstance(value, list) else [value]:
                messages.append(str(item) if key == "non_field_errors" else f"{key}: {item}")
        response.data = {"error_details": response.data,
                         "message": messages[0] if messages else "An unknown error occurred"}
    return response
