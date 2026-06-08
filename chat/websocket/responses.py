def socket_success(*, action: str, data=None, message: str = "Success", request_id=None) -> dict:
    return {
        "type": "success",
        "action": action,
        "request_id": request_id,
        "message": message,
        "data": data,
    }


def socket_event(*, action: str, data=None, message: str = "Event") -> dict:
    return {
        "type": "event",
        "action": action,
        "message": message,
        "data": data,
    }


def socket_error(*, action: str | None = None, message: str = "Something went wrong", errors=None, request_id=None) -> dict:
    return {
        "type": "error",
        "action": action,
        "request_id": request_id,
        "message": message,
        "errors": errors or {},
    }