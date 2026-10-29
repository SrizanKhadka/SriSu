"""The core-1 error vocabulary shared by HTTP, sockets and the KMP contract."""
from uuid import UUID, uuid4

from django.utils import timezone

CONTRACT = "core-1"
STATUS_CODES = {
    400: "validation_failed", 401: "unauthenticated", 403: "forbidden",
    404: "not_found", 405: "method_not_allowed", 409: "conflict",
    410: "cursor_expired", 413: "payload_too_large", 415: "unsupported_media_type",
    429: "rate_limited", 500: "server_error", 503: "temporarily_unavailable",
}
MESSAGES = {
    "feature_retired": "This feature has been retired. Update SriSu to continue.",
    "validation_failed": "Check the supplied fields.",
    "unauthenticated": "Sign in to continue.", "forbidden": "This action is not allowed.",
    "not_found": "This resource is unavailable.", "method_not_allowed": "This method is not allowed.",
    "conflict": "The resource changed. Refresh before trying again.",
    "cursor_expired": "Refresh this list.", "payload_too_large": "The request is too large.",
    "unsupported_media_type": "This content type is not supported.",
    "rate_limited": "Please wait before trying again.",
    "server_error": "The service could not complete this request.",
    "temporarily_unavailable": "The service is temporarily unavailable.",
    "encrypted_chat_unavailable": "Encrypted chat is unavailable until a supported security adapter is enabled.",
    "unknown_action": "This action is not supported.",
}


def correlation_id(value=None):
    """Accept only a UUID, never arbitrary client-controlled log text."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid4())


def error_body(status, *, code=None, fields=None, retry_after=None):
    code = code or STATUS_CODES.get(status, "server_error" if status >= 500 else "validation_failed")
    result = {
        "code": code, "message": MESSAGES.get(code, MESSAGES["server_error"]),
        "fields": fields or {}, "retryable": status in (429, 502, 503, 504),
    }
    if retry_after is not None:
        result["retry_after_seconds"] = max(0, int(retry_after))
    return result


def error_envelope(status, *, request_id=None, **kwargs):
    return {
        "error": error_body(status, **kwargs),
        "request_id": correlation_id(request_id),
        "server_time": timezone.now().isoformat(),
    }


def field_codes(value, prefix="", result=None):
    """Flatten DRF's machine codes, not user-supplied values or exception strings."""
    result = {} if result is None else result
    if isinstance(value, dict):
        for key, child in value.items():
            field_codes(child, f"{prefix}.{key}".strip("."), result)
    elif isinstance(value, (list, tuple)):
        for child in value:
            field_codes(child, prefix, result)
    elif isinstance(value, str):
        codes = result.setdefault(prefix or "non_field_errors", [])
        if value not in codes:
            codes.append(value)
    return result
