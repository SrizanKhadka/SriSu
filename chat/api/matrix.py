"""Django authorization/bootstrap boundary for the private Matrix transport."""

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from authentication.models import DeviceSession
from chat.selectors.access import authorized_rooms
from chat.services.matrix_provisioning import (
    MatrixServiceError,
    matrix_bootstrap,
    matrix_bootstrap_result_is_current,
)


class MatrixChatUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Private messaging is temporarily unavailable."
    default_code = "matrix_unavailable"
    core_error_code = "temporarily_unavailable"


def _device_session_id(request):
    auth = getattr(request, "auth", None)
    try:
        return auth.get("sid") if auth else None
    except AttributeError:
        return None


def _matrix_room_for_user(room_id, user):
    return (
        authorized_rooms(user)
        .filter(pk=room_id)
        .select_related("couple")
        .first()
    )


def _device_session_current(session_id, user):
    return bool(session_id) and DeviceSession.objects.filter(
        pk=session_id,
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


class MatrixSessionView(APIView):
    """Issue a short-lived Matrix login credential for a current relationship."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat_matrix_session"

    def post(self, request, room_id):
        room = _matrix_room_for_user(room_id, request.user)
        if room is None:
            raise NotFound()
        session_id = _device_session_id(request)
        if not _device_session_current(session_id, request.user):
            raise AuthenticationFailed("Sign in again.")
        try:
            data = matrix_bootstrap(
                room,
                request.user,
                django_session_id=str(session_id),
            )
        except MatrixServiceError:
            raise MatrixChatUnavailable() from None
        # Remote calls happen outside a DB transaction. Recheck both mutable
        # authorization inputs immediately before returning so an unlink,
        # BLOCKED edge, logout, or session expiry racing token issuance never
        # receives the freshly minted JWT.
        if _matrix_room_for_user(room_id, request.user) is None:
            raise NotFound()
        if not _device_session_current(session_id, request.user):
            raise AuthenticationFailed("Sign in again.")
        if not matrix_bootstrap_result_is_current(room_id, request.user, data):
            raise MatrixChatUnavailable()
        response = Response({"data": data})
        response["Cache-Control"] = "private, no-store"
        return response
