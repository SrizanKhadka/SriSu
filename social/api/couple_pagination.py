"""Signed, requester-bound cursors for new APIs; existing pagination is unchanged."""
from django.conf import settings
from django.core import signing
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError


class SessionExpired(APIException):
    status_code = 410
    default_detail = "Feed session expired or unavailable. Refresh to start again."
    default_code = "session_expired"


class SessionChanged(APIException):
    status_code = 409
    default_detail = "Faves changed. Refresh to start a new feed session."
    default_code = "session_changed"


def positive_int(value, field, maximum=9223372036854775807):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise ValidationError({field: "Must be a positive integer."})
    result = int(value)
    if not 1 <= result <= maximum:
        raise ValidationError({field: f"Must be between 1 and {maximum}."})
    return result


def page_size(request, state=None):
    raw = request.query_params.get("page_size")
    size = positive_int(raw, "page_size", 50) if raw is not None else (state["size"] if state else 10)
    if state and size != state["size"]:
        raise ValidationError({"page_size": "Cannot change page size during pagination."})
    return size


def encode_cursor(user, purpose, state):
    return signing.dumps({**state, "user": user.pk, "v": 1}, salt=f"social.{purpose}", compress=True)


def decode_cursor(request, purpose):
    token = request.query_params.get("cursor")
    if token is None:
        return None
    if not token or len(token) > 4096:
        raise ValidationError({"cursor": "Invalid cursor."})
    try:
        state = signing.loads(token, salt=f"social.{purpose}", max_age=settings.COUPLE_FEED_SESSION_TTL)
    except signing.SignatureExpired:
        raise SessionExpired()
    except signing.BadSignature:
        raise ValidationError({"cursor": "Invalid cursor."})
    if state.get("user") != request.user.pk or state.get("v") != 1:
        raise ValidationError({"cursor": "Invalid cursor."})
    return state


def next_url(request, purpose, state):
    return request.build_absolute_uri(request.path) + "?cursor=" + encode_cursor(request.user, purpose, state)


def keyset(queryset, state, ascending=False):
    if not state:
        return queryset
    timestamp = parse_datetime(state["last_at"])
    lookup = "gt" if ascending else "lt"
    return queryset.filter(Q(**{f"created_at__{lookup}": timestamp}) |
                           Q(created_at=timestamp, **{f"pk__{lookup}": state["last_id"]}))
