from django.urls import path
from .consumers import UserUpdatesConsumer

websocket_urlpatterns = [path("ws/couple-chat/v1/updates/", UserUpdatesConsumer.as_asgi())]
