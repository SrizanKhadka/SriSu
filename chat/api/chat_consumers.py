import json
from channels.generic.websocket import AsyncWebsocketConsumer
from chat.models import MessageModel, CoupleModel
from authentication.models import UserModel
from utils.choices import MessageType
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
import base64
import mimetypes
import base64

# class ChatConsumer(AsyncWebsocketConsumer):

#     async def connect(self):
#         self.couple_id = self.scope['url_route']['kwargs']['couple_id']
#         self.room_group_name = f"chat_{self.couple_id}"

#         print(f'ROOM GROUP NAME = {self.room_group_name}')

#         # Join room group
#         await self.channel_layer.group_add(
#             self.room_group_name,
#             self.channel_name
#         )

#         await self.accept()

#     async def disconnect(self, close_code):
#         print('DISCONNECTING WEBSOCKET')

#         # Leave room group
#         await self.channel_layer.group_discard(
#             self.room_group_name,
#             self.channel_name
#         )

#     async def receive(self, text_data):
#         data = json.loads(text_data)
#         message = data.get("message")
#         sender_id = data.get("sender_id")

#         if not message or not sender_id:
#             return

#         sender = await self.get_user(sender_id)
#         couple = await self.get_couple(self.couple_id)

#         # Create the new message in the database.
#         new_message = await self.create_message(couple, sender, message)

#         # Send message to the room group
#         await self.channel_layer.group_send(
#             self.room_group_name,
#             {
#                 "type": "chat_message",
#                 "message": new_message.text,
#                 "sender_id": new_message.sender.id,
#                 "timestamp": new_message.timestamp.isoformat()
#             },
#         )

#     async def chat_message(self, event):
#         message = event["message"]
#         sender_id = event["sender_id"]
#         timestamp = event["timestamp"]

#         # Send message to websocket
#         await self.send(text_data=json.dumps(
#             {
#                 "message": message,
#                 "sender_id": sender_id,
#                 "timestamp": timestamp,
#             }
#         ))

#     # Helper methods to interact with the database using sync_to_async
#     @sync_to_async
#     def get_user(self, sender_id):
#         try:
#             return UserModel.objects.get(id=sender_id)
#         except UserModel.DoesNotExist:
#             return None

#     @sync_to_async
#     def get_couple(self, couple_id):
#         try:
#             return CoupleModel.objects.get(id=couple_id)
#         except CoupleModel.DoesNotExist:
#             return None

#     @sync_to_async
#     def create_message(self, couple, sender, message):
#         return MessageModel.objects.create(
#             couple=couple,
#             sender=sender,
#             text=message,
#             message_type=MessageType.TEXT
#         )


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
        media = data.get("media")
        reply_to_id = data.get("reply_to")

        if not sender_id:
            return

        # Retrieve sender and couple
        sender = await self.get_user(sender_id=sender_id)
        couple = await self.get_couple(self.couple_id)

        print("Couple ID = ", self.couple_id)
        print("Sender ID = ", sender_id)

        print("SENDER = ", sender)
        print("COUPLE = ", couple)

        if not sender or not couple:
            return

        # Retrieve the reply-to message if provided
        reply_to = await self.get_message(reply_to_id) if reply_to_id else None

        print("MEDIA BASED6D = ", media)

        # # Handle media if applicable
        media_file = await self.handle_media(media, message_type) if media else None
        print("MEDIA FILE = ", media_file)

        # # Create a new message in the database
        new_message = await self.create_message(
            couple=couple,
            sender=sender,
            text=message_text,
            media=media_file,
            message_type=message_type,
            reply_to=reply_to,
        )

        # Send the message to the room group
        print("ROOM GROUP NAME = ", self.room_group_name)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.message",
                "message": {
                    "id": new_message.id,
                    "text": new_message.text,
                    "media": new_message.media.url if new_message.media else None,
                    "message_type": new_message.message_type,
                    "reply_to": (
                        new_message.reply_to.id if new_message.reply_to else None
                    ),
                    "sender_id": new_message.sender.id,
                    "is_read": new_message.is_read,
                    "is_delivered": new_message.is_delivered,
                    "timestamp": new_message.timestamp.isoformat(),
                },
                # "message": "message sent",
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
    def create_message(self, couple, sender, text, message_type, media, reply_to):
        print("I AM CREATING THE MESSAGE MODEL")
        return MessageModel.objects.create(
            couple=couple,
            sender=sender,
            text=text,
            message_type=message_type,
            media=media,
            reply_to=reply_to,
        )

    async def handle_media(self, base64_media, message_type):
        """
        Decodes base64 media data and saves it as a file.
        Returns a ContentFile object or None if media type is invalid.
        """
        try:
            # Separate the data prefix from the base64 content
            base64_data, base64_content = base64_media.split(",", 1)

            # Determine the file extension dynamically
            file_extension = self.get_file_extension(base64_data)

            if not file_extension:
                print("Unsupported or missing file type.")
                return None

            # Decode the base64 content
            media_data = base64.b64decode(base64_content)
            media_file_name = (
                f"media_{self.couple_id}_{self.scope['user'].id}.{file_extension}"
            )

            # Return the ContentFile object
            return ContentFile(media_data, name=media_file_name)

        except Exception as e:
            print(f"Error decoding media: {str(e)}")
        return None

    def get_file_extension(base64_data):
        """
        Extract the file extension from the base64-encoded data.
        """
        try:
            # Extract MIME type from the base64 string (e.g., "data:image/png;base64,....")
            mime_type = base64_data.split(";")[0].split(":")[1]
            extension = mimetypes.guess_extension(mime_type)

            if extension:
                return extension.lstrip(".")

            return None

        except Exception as e:
            print(f"Error extracting file extension: {str(e)}")
            return None
