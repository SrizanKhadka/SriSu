from django.urls import re_path

from chat.consumer.chat_consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(r"ws/chat/(?P<room_id>[0-9a-f-]+)/$", ChatConsumer.as_asgi()),
    # re_path(r"ws/chat/(?P<couple_id>\w+)/$", ChatConsumer.as_asgi()),
]