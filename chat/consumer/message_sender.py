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

    reply_to = data.get("reply_to")
    reply_to_id = reply_to.get("id") if reply_to else None
    timestamp = data.get("timestamp")

    sender = await get_user(sender_id)
    receiver = await get_user(receiver_id)
    couple = await get_couple(couple)
    
    if not sender or not chat_room:
        return
    
    reply_to = await get_message(reply_to_id) if reply_to_id else None
    
    media_ids = [
    m["id"]
    for m in data.get("medias", [])
    if "id" in m
]


    media_objects = []
    if media_ids:
        media_objects = await sync_to_async(list)(
            MediaModel.objects.filter(id__in=media_ids)
        )
    
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
         reply_to=reply_to,
    )
    
        # IMPORTANT: ManyToMany must be set AFTER save
    if media_objects:
        await sync_to_async(new_message.medias.set)(media_objects)
    
    return new_message
    
    
        
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
