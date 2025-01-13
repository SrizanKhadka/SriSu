import asyncio
import websockets
import json
import base64


def encode_file_to_base64(file_path):
    try:
        with open(file_path, "rb") as file:
            return base64.b64encode(file.read()).decode("utf-8")
    except FileNotFoundError:
        print("File not found. Check the file path.")
        return None


async def test_websocket():
    uri = "ws://127.0.0.1:8000/ws/chat/1/"
    async with websockets.connect(uri) as websocket:
        # Send a test message
        # image_path = r"C:\Users\acer\Downloads\webtest.png"
        # image_base64 = encode_file_to_base64(image_path)
        message = {
            "text": "Hello from Python WebSocket client!",
            "message_type": "IMAGE",
            "sender_id": 5,
            "media": None,  # Add file path if testing media uploads
            "reply_to": None,  # Add message ID if replying to a message
        }
        await websocket.send(json.dumps(message))
        print(f"Sent: {message}")

        # Receive a response
        response = await websocket.recv()
        print(f"Received: {response}")


asyncio.run(test_websocket())
