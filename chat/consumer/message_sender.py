from chat.utils.chatutils import *
from chat.models import MessageModel
from asgiref.sync import sync_to_async

async def handle_send_message(
    data, 
    chat_room,
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
    
    print(f"Sender ID: {sender_id}")
    print(f"Receiver ID: {receiver_id}")
    print(f"Text: {text}")

    sender = await get_user(sender_id)
    receiver = await get_user(receiver_id)
    couple = await get_couple(couple)
    
    if not sender or not chat_room:
        return

    reply_to = await get_message(reply_to_id) if reply_to_id else None

    new_message = await create_message(
        chat_room=chat_room,
        couple=couple,
        singles=singles,
        sender=sender,
        receiver=receiver,
        message_type=message_type,
        text=text,
        is_sent=True,
        timestamp=timestamp,
        medias=medias,
        reply_to=reply_to,
    )
    
    return new_message

    if new_message:
        on_message_created(new_message)
    
    
        
@sync_to_async
def create_message(**kwargs):
    try:
        medias = kwargs.pop("medias", None)
        message = MessageModel.objects.create(**kwargs)
        if medias:
            message.medias.set(medias)
        return message
    except Exception as e:
        print(f"Error creating message: {e}")
        return None
