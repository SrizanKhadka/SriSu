import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "srisu.settings")
django.setup()

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from chat.routing import websocket_urlpatterns
from chat.middleware import JwtAuthMiddleware
from couple_chat.routing import websocket_urlpatterns as couple_chat_patterns
from django.urls import re_path


application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter([
        *couple_chat_patterns,
        re_path(r"", JwtAuthMiddleware(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))),
    ]),
})
