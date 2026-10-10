"""Activity contains no message content and is never durable history."""
import time
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from .models import Device, Change
from .test_protocol import ProtocolFixtures


@override_settings(COUPLE_CHAT_PREVIEW_ENABLED=True)
class ActivityTests(ProtocolFixtures, APITransactionTestCase):
    def socket(self, client):
        from srisu.asgi import application
        return WebsocketCommunicator(application, "/ws/couple-chat/v1/updates/", headers=[
            (b"authorization", client._credentials["HTTP_AUTHORIZATION"].encode())])

    def frame(self, **extra):
        return {"version": 1, "type": "activity", "room_id": str(self.room.pk),
            "device_id": self.first_keys["device_id"], "online": True, "typing": True, **extra}

    def test_activity_disconnect_and_third_user_denial(self):
        before = Change.objects.count()
        async def flow():
            sender, receiver, third = [self.socket(c) for c in (self.first_client,self.second_client,self.third_client)]
            for socket in (sender,receiver,third):
                self.assertTrue((await socket.connect())[0]);await socket.receive_json_from()
            await sender.send_json_to(self.frame())
            event = await receiver.receive_json_from()
            self.assertEqual(event["type"], "partner.activity");self.assertTrue(event["typing"])
            self.assertLessEqual(event["typing_expires_in_ms"],5000)
            self.assertNotIn("device_id",event);self.assertNotIn("sid",event)
            self.assertTrue(await third.receive_nothing(timeout=0.05))
            await third.send_json_to(self.frame())
            self.assertEqual((await third.receive_output())["code"],4403)
            await sender.disconnect()
            self.assertFalse((await receiver.receive_json_from())["online"])
            await receiver.disconnect();await third.disconnect()
        async_to_sync(flow)()
        self.assertEqual(Change.objects.count(),before)

    def test_revoked_device_delayed_event_and_frame_limits(self):
        async def flow():
            receiver=self.socket(self.second_client)
            self.assertTrue((await receiver.connect())[0]);await receiver.receive_json_from()
            event={"type":"partner.activity","room":str(self.room.pk),"device":self.first_keys["device_id"],
                "actor":self.first.pk,"sid":self.first_sid,"online":True,"typing":True,"sent_at":time.time()-26}
            layer=get_channel_layer();group=f"couple_chat.user.{self.second.pk}"
            await layer.group_send(group,event)
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            await database_sync_to_async(lambda:Device.objects.filter(pk=self.first_keys["device_id"]).update(revoked_at=timezone.now()))()
            await layer.group_send(group,{**event,"sent_at":time.time()})
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            await receiver.disconnect()
            for payload in ("x"*513,'{"type":"activity","body":"private content is forbidden"}'):
                sender=self.socket(self.first_client)
                self.assertTrue((await sender.connect())[0]);await sender.receive_json_from()
                await sender.send_to(text_data=payload)
                self.assertEqual((await sender.receive_output())["code"],4400)
                await sender.disconnect()
        async_to_sync(flow)()
