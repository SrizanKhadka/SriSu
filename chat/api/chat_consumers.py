import json
from channels.generic.websocket import AsyncWebsocketConsumer
from chat.models import MessageModel, CoupleModel
from authentication.models import UserModel
from utils.choices import MessageType
from asgiref.sync import sync_to_async

class ChatConsumer(AsyncWebsocketConsumer):
    
    async def connect(self):
        self.couple_id = self.scope['url_route']['kwargs']['couple_id']
        self.room_group_name = f"chat_{self.couple_id}"
        
        print(f'ROOM GROUP NAME = {self.room_group_name}')
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        print('DISCONNECTING WEBSOCKET')
        
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message")
        sender_id = data.get("sender_id")
        
        if not message or not sender_id:
            return

        sender = await self.get_user(sender_id)
        couple = await self.get_couple(self.couple_id)

        # Create the new message in the database.
        new_message = await self.create_message(couple, sender, message)
        
        # Send message to the room group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": new_message.text,
                "sender_id": new_message.sender.id,
                "timestamp": new_message.timestamp.isoformat()
            },
        )
    
    async def chat_message(self, event):
        message = event["message"]
        sender_id = event["sender_id"]
        timestamp = event["timestamp"]
        
        # Send message to websocket
        await self.send(text_data=json.dumps(
            {
                "message": message,
                "sender_id": sender_id,
                "timestamp": timestamp,
            }
        ))

    # Helper methods to interact with the database using sync_to_async
    @sync_to_async
    def get_user(self, sender_id):
        try:
            return UserModel.objects.get(id=sender_id)
        except UserModel.DoesNotExist:
            return None 
    
    @sync_to_async
    def get_couple(self, couple_id):
        try:
            return CoupleModel.objects.get(id=couple_id)
        except CoupleModel.DoesNotExist:
            return None 
    
    @sync_to_async
    def create_message(self, couple, sender, message):
        return MessageModel.objects.create(
            couple=couple,
            sender=sender,
            text=message,
            message_type=MessageType.TEXT
        )
