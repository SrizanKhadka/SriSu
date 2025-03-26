import json
from channels.generic.websocket import AsyncWebsocketConsumer
from chat.models import MessageModel, CoupleModel
from authentication.models import UserModel
from utils.choices import MessageType
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError

class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.couple_id = self.scope["url_route"]["kwargs"]["couple_id"]
        self.room_group_name = f"chat_{self.couple_id}"

        # Join room group
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        print("INSIDE RECEIVE FUNCTION")
        data = json.loads(text_data)

        # Extract data from the payload
        sender_id = data.get("sender_id")
        message_type = data.get("message_type", MessageType.TEXT)
        message_text = data.get("text", "")
        media_url = data.get("media_url")
        reply_to_id = data.get("reply_to")

        if not sender_id:
            return

        # Retrieve sender and couple
        sender = await self.get_user(sender_id=sender_id)
        couple = await self.get_couple(self.couple_id)


        if not sender or not couple:
            return
        
        if not self.validateURL(media_url):
            print('MEDIA URL NOT VALID ',media_url)
            return

        # Retrieve the reply-to message if provided
        reply_to = await self.get_message(reply_to_id) if reply_to_id else None


        #Create a new message in the database
        new_message = await self.create_message(
            couple=couple,
            sender=sender,
            text=message_text,
            media_url = media_url,
            message_type=message_type,
            reply_to=reply_to,
        )
        
        print('MESSAGE MODEL CREATED')

        # Send the message to the room group
        print("ROOM GROUP NAME = ", self.room_group_name)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.message",
                "message": {
                    "id": new_message.id,
                    "text": new_message.text,
                    "media_url": new_message.media_url if new_message.media_url else None,
                    "message_type": new_message.message_type,
                    "reply_to": (
                        new_message.reply_to.id if new_message.reply_to else None
                    ),
                    "sender_id": new_message.sender.id,
                    "is_read": new_message.is_read,
                    "is_delivered": new_message.is_delivered,
                    "timestamp": new_message.timestamp.isoformat(),
                },
                "message": "message sent",
            },
        )

    async def chat_message(self, event):
        # Forward the message to the websocket
        print("INSIDE CHAT MESSAGE FUNCTION")
        await self.send(text_data=json.dumps(event["message"]))

    # Helper methods
    @sync_to_async
    def get_user(self, sender_id):
        try:
            return UserModel.objects.get(id=sender_id)
        except UserModel.DoesNotExist:
            return None

    @sync_to_async
    def get_couple(self, couple_id):
        try:
            couple = CoupleModel.objects.filter(id=couple_id).first()
            print("COUPLE = ", couple)
            return couple
        except:
            return None

    @sync_to_async
    def get_message(self, message_id):
        return MessageModel.objects.filter(id=message_id).first()

    @sync_to_async
    def create_message(self, couple, sender, text, message_type, media_url, reply_to):
        print("I AM CREATING THE MESSAGE MODEL")
        return MessageModel.objects.create(
            couple=couple,
            sender=sender,
            text=text,
            message_type=message_type,
            media_url=media_url,
            reply_to=reply_to,
        )
    
    validate = URLValidator()

    def validateURL(self,url):
        try:
            self.validate(url)
            return True
        except ValidationError as e:
         print('Inavlid url:', e)
        return False