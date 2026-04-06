from datetime import datetime
from authentication.models import UserModel
from authentication.api.serializers import UserModelSerializer
from chat.models import ChatRoom
from channels.db import database_sync_to_async
from django.db import transaction
from chat.models import MessageModel
from chat.api.serializers import ChatRoomSerializer
from django.db.models import Q
from chat.utils.chatutils import serialize_message,serialize_message_sync


def get_other_user(chat_room, me):
    if chat_room.user_one_id == me.id:
        return chat_room.user_two
    if chat_room.user_two_id == me.id:
        return chat_room.user_one
    return None

def update_unread_count(chat_room):
    messages = chat_room.message_models.all()

    unread_count = {}

    for user in (chat_room.user_one, chat_room.user_two):
        if not user:
            continue

        unread_count[str(user.id)] = (
            messages
            .filter(is_read=False)
            .exclude(sender=user)
            .count()
        )

    chat_room.unread_count = unread_count
    chat_room.save(update_fields=["unread_count"])



def serialize_chat_rooms_sync(chat_rooms, me, scope=None):
    data = []

    for room in chat_rooms:
        other_user = get_other_user(room, me)
        if not other_user:
            continue
        
        last_message = (
            room.message_models
            .order_by("-timestamp")
            .first()
        )
        
        update_unread_count(room)

        data.append({
            "chat_room": ChatRoomSerializer(room).data,
            "other_user": UserModelSerializer(
                other_user, context={"scope": scope}
            ).data,
            "last_message": last_message, 
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
    
async def get_and_serialize_chat_rooms(
    user, limit=20, scope=None, last_updated: str | None = None
):
    rooms = await get_chat_rooms_for_user(
        user, limit=limit, last_updated=last_updated
    )

    raw_data = await serialize_chat_rooms(
        rooms, user, scope=scope
    )

    for item in raw_data:
        last_message = item.pop("last_message", None)

        if last_message:
            serialized = await serialize_message(last_message, scope)
        else:
            serialized = None

        # inject into chat_room payload
        item["chat_room"]["last_message"] = serialized

    next_cursor = rooms[-1].updated_at.isoformat() if rooms else None

    return {
        "chat_rooms": raw_data,
        "next_cursor": next_cursor,
        "limit": limit,
    }
    
@database_sync_to_async
def update_chat_room_last_message(
    scope,
    chat_room_id,
    last_message: MessageModel
):
    
    chat_room = ChatRoom.objects.get(id=chat_room_id)
    print("Updating chat room last message:", chat_room)
    chat_room.last_message = last_message
    update_unread_count(chat_room)
    chat_room.updated_at = datetime.now()
    chat_room.save(
        update_fields=["last_message", "updated_at"]
    )
    
    chat_room_data = ChatRoomSerializer(chat_room).data
    serialized_last_message = serialize_message_sync(
        last_message, scope
    )
    chat_room_data["last_message"] = serialized_last_message
    return chat_room_data




@database_sync_to_async
def set_user_typing(chat_room, user_id: int, is_typing: bool):
    user_id_str = str(user_id)

    # Start an atomic database transaction
    # select_for_update() locks this ChatRoom row until the transaction ends
    with transaction.atomic():

        # Re-fetch and LOCK the chat room row to prevent concurrent overwrites
        chat_room = ChatRoom.objects.select_for_update().get(id=chat_room)
        typing_data = chat_room.is_typing or {}

        if is_typing:
            typing_data[user_id_str] = True
        else:
            typing_data.pop(user_id_str, None)

        chat_room.is_typing = typing_data
        chat_room.save(update_fields=["is_typing"])

    return typing_data
