import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://127.0.0.1:8000/ws/chat/1/"
    async with websockets.connect(uri) as websocket:
        # Send a test message
        message = {
            "text": "Hello from Python WebSocket client!",
            "message_type": "IMAGE",
            "sender_id": 5,
            "media_url": "http://localhost:8000/media/chats_media/me.PNG",
            "reply_to": None,  # Add message ID if replying to a message
        }
        await websocket.send(json.dumps(message))
        print(f"Sent: {message}")

        # Receive a response
        response = await websocket.recv()
        print(f"Received: {response}")


    # asyncio.run(test_websocket())

async def send_message():
    uri = "ws://localhost:8000/ws/chat/7fe512b9-548b-4a21-93cd-0a25d1aed5b4/"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "action": "send_message",
            "text": "I am fine!",
            "sender_id": 97,
            "receiver_id": 95,
            "couple":2,
            "chat_room": "7fe512b9-548b-4a21-93cd-0a25d1aed5b4",
            "message_type": "text"
        }))

        response = await websocket.recv()
        print("Response:", response)

    asyncio.run(send_message())

async def editMessage():
    uri = "ws://localhost:8000/ws/chat/3d0504c6-21ac-40dc-963a-97fb75adf711/"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "action": "edit_message",
            "message_id": 15,
            "sender_id": 27,
            "text": "I am fine!",
            "receiver_id": 26,
            "couple":2,
            "chat_room": "3d0504c6-21ac-40dc-963a-97fb75adf711",
            "message_type": "text"
        }))

        response = await websocket.recv()
        print("Response:", response)

# asyncio.run(editMessage())

async def markMessagesRead():
    uri = "ws://localhost:8000/ws/chat/3d0504c6-21ac-40dc-963a-97fb75adf711/"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "action": "mark_messages_read",
            "receiver_id": 26,
            "couple":2,
            "chat_room": "3d0504c6-21ac-40dc-963a-97fb75adf711",
        }))

        response = await websocket.recv()
        print("Response:", response)

# asyncio.run(markMessagesRead())

async def deleteMessage():
    uri = "ws://localhost:8000/ws/chat/3d0504c6-21ac-40dc-963a-97fb75adf711/"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "action": "delete_message",
            "message_id": [14,15],
            "user_id": 26,
            "sender_id": 26,
            "receiver_id": 27,
            "couple":2,
            "delete_option": "DELETE_FOR_EVERYONE",
            "chat_room": "3d0504c6-21ac-40dc-963a-97fb75adf711",
        }))

        response = await websocket.recv()
        print("Response:", response)

# asyncio.run(deleteMessage())


# if __name__ == "deletemessage":
#     deleteMessage()

if __name__ == "__send_message__":
    send_message()
    
    