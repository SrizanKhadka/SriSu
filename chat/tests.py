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


asyncio.run(test_websocket())
