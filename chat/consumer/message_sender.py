from chat.utils.chatutils import *

class MessageSender:
    async def handle_send_message(self, data, chat_room):
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

        # reply_to = await self.get_message(reply_to_id) if reply_to_id else None

        new_message = await self.create_message(
            chat_room=self.chat_room,
            couple=couple,
            singles=singles,
            sender=sender,
            receiver=receiver_id,
            message_type=message_type,
            text=text,
            is_delivered=True,
            timestamp=timestamp,
            medias=medias,
            reply_to=None,
        )

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "chat.message", "message": self.serialize_message(new_message)},
        )
    
    @sync_to_async
    def create_message(self, **kwargs):
        return MessageModel.objects.create(**kwargs)
