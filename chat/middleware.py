"""Native bearer headers plus the legacy query-token adapter; never log either."""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from authentication.sessions import SessionJWTAuthentication as JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


@database_sync_to_async
def authenticate_token(token):
    try:
        auth = JWTAuthentication()
        validated = auth.get_validated_token(token)
        user = auth.get_user(validated)
        if not user.is_phone_verified or not user.is_profile_complete:
            return AnonymousUser(), None
        user.device_session_id = validated.get('sid')
        return user, int(validated['exp'])
    except (AuthenticationFailed, InvalidToken, TokenError, KeyError, ValueError, TypeError):
        return AnonymousUser(), None


@database_sync_to_async
def user_is_active(user_id, session_id=None):
    from authentication.models import UserModel
    if not UserModel.objects.filter(pk=user_id, is_active=True, is_phone_verified=True, is_profile_complete=True).exists():
        return False
    if session_id:
        from authentication.models import DeviceSession
        from django.utils.timezone import now
        return DeviceSession.objects.filter(pk=session_id, user_id=user_id, revoked_at__isnull=True, expires_at__gt=now()).exists()
    return settings.AUTH_ACCEPT_LEGACY_TOKENS


async def get_user_from_token(token):
    return (await authenticate_token(token))[0]


class JwtAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        headers = dict(scope.get('headers', []))
        origin = headers.get(b'origin')
        if origin and origin.decode(errors='replace') not in settings.WEBSOCKET_ALLOWED_ORIGINS:
            await send({'type': 'websocket.close', 'code': 4403})
            return
        token = None
        authorization = headers.get(b'authorization', b'').decode(errors='replace')
        if authorization.startswith('Bearer '):
            token = authorization[7:]
        elif not authorization:
            values = parse_qs(scope.get('query_string', b'').decode(errors='replace')).get('token', [])
            token = values[0] if len(values) == 1 else None
        scope['user'], scope['access_expires_at'] = await authenticate_token(token) if token else (AnonymousUser(), None)
        if token and scope['user'].is_authenticated:
            scope['device_session_id'] = getattr(scope['user'], 'device_session_id', None)
        # The consumer needs the validated principal/expiry, never the credential.
        scope['query_string'] = b''
        scope['headers'] = [(key, value) for key, value in scope.get('headers', []) if key != b'authorization']
        return await super().__call__(scope, receive, send)
