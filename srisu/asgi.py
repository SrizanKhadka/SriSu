"""
ASGI config for srisu project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from channels.auth import AuthMiddlewareStack
from chat.routing import websocket_urlpatterns



os.environ.setdefault("DJANGO_SETTINGS_MODULE", "srisu.settings")

django_asgi_app = get_asgi_application()


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        )
    }
)

#considerations.


# from urllib.parse import parse_qs
# from channels.middleware import BaseMiddleware
# from django.contrib.auth.models import AnonymousUser
# from rest_framework_simplejwt.tokens import UntypedToken
# from rest_framework_simplejwt.authentication import JWTAuthentication
# from django.db import close_old_connections

# class JWTAuthMiddleware(BaseMiddleware):
#     async def __call__(self, scope, receive, send):
#         # Get the token from query params (e.g., ?token=abcd.efgh.ijkl)
#         query_string = scope["query_string"].decode()
#         query_params = parse_qs(query_string)
#         token = query_params.get("token")

#         if token:
#             try:
#                 validated_token = JWTAuthentication().get_validated_token(token[0])
#                 user = JWTAuthentication().get_user(validated_token)
#                 scope["user"] = user
#             except Exception:
#                 scope["user"] = AnonymousUser()
#         else:
#             scope["user"] = AnonymousUser()

#         close_old_connections()
        # return await super().__call__(scope, receive, send)

# from channels.routing import ProtocolTypeRouter, URLRouter
# from chat.middleware.jwt_middleware import JWTAuthMiddleware
# from django.core.asgi import get_asgi_application
# from chat.routing import websocket_urlpatterns

# django_asgi_app = get_asgi_application()

# application = ProtocolTypeRouter({
#     "http": django_asgi_app,
#     "websocket": JWTAuthMiddleware(
#         URLRouter(websocket_urlpatterns)
#     ),
# })

# const socket = new WebSocket(
#   "wss://yourdomain.com/ws/chatroom/?token=YOUR_JWT_TOKEN"
# );

