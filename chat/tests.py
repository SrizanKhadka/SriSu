from django.test import TestCase

# Create your tests here.
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://127.0.0.1:8000/ws/chat/1/"  
    async with websockets.connect(uri) as websocket:
        # Send a test message
        message = {
            "message": "Hello from Python WebSocket client!",
            "sender_id": 5,  
        }
        await websocket.send(json.dumps(message))
        print(f"Sent: {message}")

        # Receive a response
        response = await websocket.recv()
        print(f"Received: {response}")

asyncio.run(test_websocket())