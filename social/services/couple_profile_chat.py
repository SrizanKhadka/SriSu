"""Profile cards reuse chat persistence, authorization, events and HTTP recovery."""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from chat.models import ChatRoom, MessageModel
from chat.websocket.responses import socket_event
from chat.selectors.access import authorized_rooms
from chat.services.message_service import send_message, SendMessageInput
from social.services.couple_profile_sections import ProfileConflict


def notify(room_id, users):
    # No private content is put in invalidation events. Existing clients converge
    # through authorized HTTP history even if this best-effort publication fails.
    payload = {"type": "chat.broadcast", "room_id": room_id, "payload": socket_event(action="chat_room_updated", data={"id": room_id})}
    try:
        layer = get_channel_layer()
        for user_id in users:
            async_to_sync(layer.group_send)(f"chat_user_{user_id}", payload)
        async_to_sync(layer.group_send)(f"chat_room_{room_id}", payload)
    except Exception:
        logging.getLogger(__name__).warning("profile_chat_publication_unavailable")


def send_profile_card(user, couple, members, request_id, action, text):
    existing = MessageModel.objects.filter(sender=user, profile_request_id=request_id).first()
    if existing:
        if existing.couple_id != couple.pk or existing.profile_action != action:
            raise ProfileConflict()
        return existing, False
    users = sorted(m.user_id for m in members)
    room = authorized_rooms(user).filter(couple=couple, user_one_id=users[0], user_two_id=users[1]).first()
    if room is None:
        if ChatRoom.objects.filter(user_one_id=users[0], user_two_id=users[1]).exists():
            raise ProfileConflict("Your partner chat has changed. Reopen it before sending.")
        room = ChatRoom.objects.create(user_one_id=users[0], user_two_id=users[1], couple=couple, chat_type="couple")
    message, _ = send_message(user=user, payload=SendMessageInput(chat_room_id=str(room.pk), text=text))
    message.profile_action = action
    message.profile_request_id = request_id
    message.save(update_fields=["profile_action", "profile_request_id"])
    transaction.on_commit(lambda: notify(str(room.pk), users))
    return message, True
