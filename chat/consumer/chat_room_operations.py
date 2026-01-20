from datetime import datetime
from authentication.models import UserModel
from authentication.api.serializers import UserModelSerializer as UserSerializer
from chat.models import ChatRoom
from channels.db import database_sync_to_async
from django.db import transaction
from chat.models import MessageModel
from chat.api.serializers import ChatRoomSerializer
from django.db.models import Q

def get_other_user(chat_room, me):
    if chat_room.user_one_id == me.id:
        return chat_room.user_two
    if chat_room.user_two_id == me.id:
        return chat_room.user_one
    return None

def serialize_chat_rooms_sync(chat_rooms, me):
    data = []

    for room in chat_rooms:
        other_user = get_other_user(room, me)
        if not other_user:
            continue

        data.append({
            "chat_room": ChatRoomSerializer(room).data,
            "other_user": UserSerializer(other_user).data,
            # "last_message": (
            #     MessageSerializer(room.last_message).data
            #     if room.last_message else None
            # ),
        })

    return data


def get_chat_rooms_for_user_sync(user, limit=20, last_updated: str | None = None):
    """
    Fetch chat rooms for a user using cursor-based pagination.
    :param user: UserModel instance
    :param limit: Number of chat rooms to fetch
    :param last_updated: ISO timestamp of last chat room fetched (cursor)
    :return: list of ChatRoom instances
    """
    qs = ChatRoom.objects.filter(Q(user_one=user) | Q(user_two=user))

    # If last_updated is provided, fetch chat rooms older than this timestamp
    if last_updated:
        try:
            cursor_time = datetime.fromisoformat(last_updated)
            qs = qs.filter(updated_at__lt=cursor_time)
        except ValueError:
            pass  # Invalid timestamp, ignore cursor

    qs = qs.select_related(
        "user_one",
        "user_two",
        "singles",
        "last_message",
    ).order_by("-updated_at")[:limit]

    return list(qs)

    
get_chat_rooms_for_user = database_sync_to_async(
    get_chat_rooms_for_user_sync
)

serialize_chat_rooms = database_sync_to_async(
    serialize_chat_rooms_sync
)

@database_sync_to_async
def get_and_serialize_chat_rooms(user, limit=20, last_updated: str | None = None):
    rooms = get_chat_rooms_for_user_sync(user, limit=limit, last_updated=last_updated)
    data = serialize_chat_rooms_sync(rooms, user)
    
    # Determine the next cursor
    next_cursor = None
    if rooms:
        next_cursor = rooms[-1].updated_at.isoformat()

    return {
        "chat_rooms": data,
        "next_cursor": next_cursor,
        "limit": limit
    }

@database_sync_to_async
def set_user_typing(chat_room: ChatRoom, user_id: int, is_typing: bool):
    user_id_str = str(user_id)

    # Start an atomic database transaction
    # select_for_update() locks this ChatRoom row until the transaction ends
    with transaction.atomic():

        # Re-fetch and LOCK the chat room row to prevent concurrent overwrites
        chat_room = ChatRoom.objects.select_for_update().get(id=chat_room.id)
        typing_data = chat_room.is_typing or {}

        if is_typing:
            typing_data[user_id_str] = True
        else:
            typing_data.pop(user_id_str, None)

        chat_room.is_typing = typing_data
        chat_room.save(update_fields=["is_typing"])

    return typing_data
