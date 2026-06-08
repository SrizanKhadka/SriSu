from django.urls import re_path

from chat.consumers.chat_consumers import ChatConsumer

websocket_urlpatterns = [
    # re_path(r"ws/chat/", ChatConsumer.as_asgi()),
    # re_path(r"ws/chat/(?P<couple_id>\w+)/$", ChatConsumer.as_asgi()),
    re_path(r"ws/chat/$", ChatConsumer.as_asgi()),

]