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
    uri = "ws://localhost:8000/ws/chat/3d0504c6-21ac-40dc-963a-97fb75adf711/"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "type": "send_message",
            "text": "Script Test!",
            "sender_id": 27,
            "receiver_id": 26,
            "chat_room": "3d0504c6-21ac-40dc-963a-97fb75adf711",
            "message_type": "text"
        }))

        while True:
            try:
                response = await websocket.recv()
                print("Response:", response)
            except websockets.ConnectionClosed:
                print("Connection closed")
                break
            
asyncio.run(send_message())

if __name__ == "__send_message__":
    send_message()
    
    