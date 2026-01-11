from urllib.parse import parse_qs
from django.contrib.auth.models import AnonymousUser
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from rest_framework_simplejwt.authentication import JWTAuthentication


@database_sync_to_async
def get_user_from_token(token):
    try:
        validated_token = JWTAuthentication().get_validated_token(token)
        return JWTAuthentication().get_user(validated_token)
    except Exception:
        return AnonymousUser()


class JwtAuthMiddleware(BaseMiddleware):
    """
    JWT auth for WebSocket connections.
    Token can be passed via query params (?token=...)
    """

    async def __call__(self, scope, receive, send):
        scope["user"] = AnonymousUser()

        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        token = query_params.get("token")

        if token:
            scope["user"] = await get_user_from_token(token[0])

        return await super().__call__(scope, receive, send)
