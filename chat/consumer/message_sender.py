from chat.utils.chatutils import get_user, get_message
from chat.models import MessageModel
from asgiref.sync import sync_to_async

async def handle_send_message(
    data, 
    chat_room,
    on_message_created
):
    couple = data.get("couple")
    singles = data.get("single")
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    text = data.get("text", "")
    message_type = data.get("message_type", "text")
    medias = data.get("medias")
    reply_to_id = data.get("reply_to")
    timestamp = data.get("timestamp")

    sender = await get_user(sender_id)
    if not sender or not chat_room:
        return

    reply_to = await get_message(reply_to_id) if reply_to_id else None

    new_message = await create_message(
        chat_room=chat_room,
        couple=couple,
        singles=singles,
        sender=sender,
        receiver=receiver_id,
        message_type=message_type,
        text=text,
        is_delivered=True,
        timestamp=timestamp,
        medias=medias,
        reply_to=reply_to,
    )

    if new_message:
        on_message_created(new_message)

@sync_to_async
def create_message(**kwargs):
    try:
        return MessageModel.objects.create(**kwargs)
    except Exception as e:
        print(f"Error creating message: {e}")
        return None