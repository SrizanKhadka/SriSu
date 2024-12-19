import json
from channels.generic.websocket import AsyncWebsocketConsumer
from chat.models import MessageModel,CoupleModel
from authentication.models import UserModel
from utils.choices import MessageType

class ChatConsumer(AsyncWebsocketConsumer):
    
    async def connect(self):
       self.couple_id = self.scope['url_route']['kwargs']['couple_id']
       self.room_group_name = f"chat_{self.couple_id}"
       
       #Join room group
       print('ROOM GROUP NAME = ',self.room_group_name)
       
       await self.channel_layer.group_add(
           self.room_group_name,
           self.channel_name
       )
       
       await self.accept()
    
    async def disconnect(self, close_code):
        #Leave room group
        
        print('DISCONNECT THE WEBSOCKETS')
        
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self,text_data):
        data = json.loads(text_data)
        message = data["message"]
        sender_id = data["sender_id"]
        
        #save message to the database.
        sender = await UserModel.objects.aget(id=sender_id)
        couple = await CoupleModel.objects.aget(id=self.couple_id)
        
        #aget method is an asynchronous version of the get method.
        
        new_message = await MessageModel.objects.create(
            couple=couple,
            sender=sender,
            text=message,
            message_type=MessageType.TEXT
        )
        
        await self.channel_layer.group.send(
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
        
        #send message to websocket
        await self.send(text_data=json.dumps(
            {
            "message": message,
            "sender_id": sender_id,
            "timestamp": timestamp,
            }
        ))
        