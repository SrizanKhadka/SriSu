from uuid import uuid4
from django.utils import timezone
from srisu.api.errors import error_body


def socket_success(*, action: str, data=None, message: str = "Success", request_id=None) -> dict:
    return {
        "type": "success",
        "action": action,
        "request_id": request_id,
        "message": message,
        "data": data,
        "protocol_version": 1,
    }


def socket_event(*, action: str, data=None, message: str = "Event") -> dict:
    return {
        "type": "event",
        "action": action,
        "message": message,
        "data": data,
        "protocol_version": 1,
        "event_id": str(uuid4()),
        "emitted_at": timezone.now().isoformat(),
    }


def socket_error(*, action: str | None = None, message: str = "Something went wrong", errors=None, request_id=None, status=400, code=None, fields=None) -> dict:
    return {
        "type": "error",
        "action": action,
        "request_id": request_id,
        "message": message,
        "errors": errors or {},
        "protocol_version": 1,
        "error": error_body(status, code=code, fields=fields),
    }
