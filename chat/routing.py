from django.urls import re_path

from chat.api.chat_consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(r"ws/chat/(?P<couple_id>\w+)/$", ChatConsumer.as_asgi()),
]