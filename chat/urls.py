from django.urls import path

from chat.api.matrix import MatrixSessionView

urlpatterns = [
    path(
        "v2/rooms/<uuid:room_id>/matrix/session/",
        MatrixSessionView.as_view(),
        name="chat-v2-matrix-session",
    ),
]
