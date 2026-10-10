"""Disposable native-adapter ↔ Django HTTP test, using synthetic accounts only.

The evaluation APK/XCTest targets execute the production libsignal/storage code.
The host relays public keys and synthetic envelopes. This is not a full UI test.
Never point this runner at an existing database or deployed API.
"""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", required=True)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", default="emulator-5580")
    parser.add_argument("--mac-host", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--mac-root", required=True)
    parser.add_argument("--spark-stress-messages", type=int, default=2100)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    reports = root / ".cache/native-chat-e2e" / str(uuid4())
    reports.mkdir(parents=True)
    sys.path.insert(0, str(root))
    os.environ["DJANGO_SETTINGS_MODULE"] = "srisu.workspace_test_settings"
    from django.conf import settings
    settings.DATABASES["default"]["NAME"] = str(reports / "disposable.sqlite3")
    settings.COUPLE_CHAT_PREVIEW_ENABLED = True
    settings.MEDIA_ROOT = str(reports / "media")
    settings.LOGGING["loggers"]["srisu"]["level"] = "ERROR"
    import django
    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from authentication.models import UserModel
    from authentication.sessions import create_session
    from couple_chat.models import Message, Operation
    from social.services.relationship_service import invite, transition
    from utils.choices import CoupleConnectionStatus as Status
    with redirect_stdout(io.StringIO()):
        call_command("migrate", verbosity=0, interactive=False)
    users = [UserModel.objects.create_user(phone_number=f"+977980090000{i}", full_name=f"Synthetic participant {i}",
        is_phone_verified=True,is_profile_complete=True) for i in range(1,4)]
    invitation, _ = invite(users[0], users[1].phone_number)
    room = transition(users[1], invitation.pk, Status.ACCEPTED, users[0].phone_number, users[1].phone_number)[2]
    sessions = {u.pk:create_session(u) for u in users}
    credentials = {key:value["access"] for key,value in sessions.items()}
    module_spec = importlib.util.spec_from_file_location("production_relay", Path(args.frontend) / "tools/e2ee-spike/production_relay.py")
    relay_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(relay_module)
    relay = relay_module.ProductionRelay(adb=args.adb, serial=args.serial, mac_host=args.mac_host, identity=args.identity,
        known_hosts=args.known_hosts, mac_root=args.mac_root, reports=reports)
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args): pass
    server = make_server("127.0.0.1",0,get_wsgi_application(),handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/api/couple-chat/v1/"
    def api(user, path, value=None, expected=200):
        request = Request(base+path,data=json.dumps(value).encode() if value is not None else None,
            headers={"Authorization":"Bearer "+credentials[user.pk],"Content-Type":"application/json"})
        try:
            with urlopen(request,timeout=20) as response: status, payload = response.status,json.load(response)
        except HTTPError as error: status,payload = error.code,json.load(error)
        if status != expected:
            # No payload/token logging even for a failing test.
            raise AssertionError(f"Expected HTTP {expected}, got {status} for {path.split('?')[0]}")
        return payload.get("data",payload)
    room_path = f"rooms/{room.pk}/"
    devices = {}
    def command(platform, index, name, value=None): return relay.call(platform,users[index].pk,name,value)
    def incoming(platform,index,op):
        sender = 1-index
        return command(platform,index,"open",{"room_id":str(room.pk),"peer_user_id":str(users[sender].pk),
            "peer_identity":devices[sender]["identity_key"],"operation":op})
    manifest = {"scope":"production native adapters and live loopback Django HTTP; UI not exercised","checks":[]}
    try:
        for platform,index in (("android",0),("ios",1)):
            command(platform,index,"reset",{"confirm_history_loss":True})
            registration = command(platform,index,"registration")
            devices[index] = api(users[index],"devices/",registration)
            assert api(users[index],"devices/",registration) == devices[index]
        print("Both native devices registered through authenticated HTTP",flush=True)
        fingerprints={}
        for platform,index in (("android",0),("ios",1)):
            fingerprints[index]=command(platform,index,"fingerprint",{
                "peer_user_id":str(users[1-index].pk),"peer_identity":devices[1-index]["identity_key"]})
        assert fingerprints[0]["display"]==fingerprints[1]["display"]
        for platform,index in (("android",0),("ios",1)):
            compared=command(platform,index,"fingerprint",{"peer_user_id":str(users[1-index].pk),
                "peer_identity":devices[1-index]["identity_key"],"scan":fingerprints[1-index]["qr"]})
            assert compared["matches"] is True
        manifest["checks"].append("Android/iOS safety number equality and bidirectional native QR comparison")
        for platform,index in (("android",0),("ios",1)):
            peer = devices[1-index]
            claim_id = str(uuid4())
            value = {"device_id":devices[index]["id"],"recipient_device_id":peer["id"],"operation_id":claim_id}
            bundle = api(users[index],room_path+"bundles/claim/",value)
            assert bundle == api(users[index],room_path+"bundles/claim/",value)
            command(platform,index,"establish",{"room_id":str(room.pk),"claim_id":claim_id,"bundle":bundle})
        manifest["checks"].append("registration and public-bundle retry")
        cursor = {0:0,1:0}
        def send(platform,index,kind="message",target=None,version=0,content=None,message_id=None,attachments=None):
            id = message_id or str(uuid4())
            op = {"device_id":devices[index]["id"],"recipient_device_id":devices[1-index]["id"],"operation_id":id,
                "target_id":target or id,"kind":kind,"version":version,"reply_to":None}
            if attachments: op["attachments"]=attachments
            body = {"version":1,"content":content or {"type":"text","body":f"Synthetic {platform} नमस्ते 👋","quote":None}}
            native_input = {"room_id":str(room.pk),"peer_user_id":str(users[1-index].pk),"peer_identity":devices[1-index]["identity_key"],"operation":op,"content":body}
            sealed = command(platform,index,"seal",native_input)
            # A fresh native process, then a lost-ack simulation: retransmit exactly.
            assert command(platform,index,"seal",native_input) == sealed
            accepted = api(users[index],room_path+"operations/",sealed)
            assert api(users[index],room_path+"operations/",sealed) == accepted
            api(users[2],room_path+"operations/",sealed,404)
            return id,body,sealed
        sent=[]
        for sender,receiver,index in (("android","ios",0),("ios","android",1)):
            id,body,sealed=send(sender,index)
            page = api(users[1-index],room_path+f"changes/?device_id={devices[1-index]['id']}&after={cursor[1-index]}")
            received = next(c["operation"] for c in page["changes"] if c.get("operation",{} ) and c["operation"]["operation_id"]==id)
            altered = {**received,"kind":"delete"}
            result = relay.exchange(receiver,[{"account":str(users[1-index].pk),"command":"open","input":{
                "room_id":str(room.pk),"peer_user_id":str(users[index].pk),"peer_identity":devices[index]["identity_key"],"operation":altered}}])[0]
            assert result.get("ok") is False
            assert incoming(receiver,1-index,received) == body
            assert incoming(receiver,1-index,received) == body
            cursor[1-index]=page["cursor"]
            sent.append(id)
            manifest["checks"].append(sender+" to "+receiver+" after restart, uncertain ack, tamper rollback, receive replay")
            print(sender+" -> "+receiver+": encrypted HTTP delivery and restart retry passed",flush=True)
        # Native-authenticated mutations take the same ordered HTTP path.
        for kind,content in (("reaction",{"type":"reaction","emoji":"❤️"}), ("receipt.read",{"type":"receipt"})):
            id,body,_=send("ios",1,kind,sent[0],1,content)
            page=api(users[0],room_path+f"changes/?device_id={devices[0]['id']}&after={cursor[0]}")
            received=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==id)
            assert incoming("android",0,received)==body
            cursor[0]=page["cursor"]
        id,body,_=send("android",0,"delete",sent[0],1,{"type":"delete"})
        page=api(users[1],room_path+f"changes/?device_id={devices[1]['id']}&after={cursor[1]}")
        received=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==id)
        assert received["target_deleted"] is True and incoming("ios",1,received)==body
        assert Message.objects.count()==2 and Operation.objects.count()==5
        assert not any("Synthetic" in ciphertext for ciphertext in Operation.objects.values_list("ciphertext",flat=True))
        manifest["checks"].append("ordered encrypted reaction/read/deletion, third-user denial, no logical duplicates")
        # Real native media bytes use the private upload lifecycle. No key or
        # readable descriptor is included in any HTTP request outside E2EE.
        for sender,receiver,index in (("android","ios",0),("ios","android",1)):
            message_id,attachment_id=str(uuid4()),str(uuid4())
            plain=b"synthetic-media-fixture-"+bytes(range(256))*16
            descriptor=relay.binary(sender,users[index].pk,"media_seal",{"room_id":str(room.pk),"message_id":message_id,"id":attachment_id},plain)
            encrypted=relay.read_media(sender,users[index].pk,descriptor)
            reserved=api(users[index],room_path+"attachments/",{"attachment_id":attachment_id,"message_id":message_id,"device_id":devices[index]["id"],
                "ciphertext_size":len(encrypted),"sha256":descriptor["sha256"]})
            assert reserved["state"]=="pending"
            request=Request(base+room_path+f"attachments/{attachment_id}/upload/",data=encrypted,method="PUT",headers={
                "Authorization":"Bearer "+credentials[users[index].pk],"Content-Type":"application/octet-stream","X-Chat-Device":devices[index]["id"]})
            with urlopen(request,timeout=20) as response: assert response.status==200
            api(users[index],room_path+f"attachments/{attachment_id}/finalize/",{"device_id":devices[index]["id"]})
            id,body,_=send(sender,index,content={"type":"media","items":[{"descriptor":descriptor,"kind":"photo","mime":"image/jpeg"}],"caption":"Synthetic fixture"},
                message_id=message_id,attachments=[attachment_id])
            page=api(users[1-index],room_path+f"changes/?device_id={devices[1-index]['id']}&after={cursor[1-index]}")
            received=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==id)
            opened=incoming(receiver,1-index,received)
            assert opened==body
            remote_descriptor=opened["content"]["items"][0]["descriptor"]
            token=api(users[1-index],room_path+f"attachments/{attachment_id}/capability/",{"device_id":devices[1-index]["id"]})["capability"]
            request=Request(base+room_path+f"attachments/{attachment_id}/content/",headers={"Authorization":"Bearer "+credentials[users[1-index].pk],"X-Chat-Media-Capability":token})
            with urlopen(request,timeout=20) as response: downloaded=response.read()
            assert downloaded==encrypted
            relay.binary(receiver,users[1-index].pk,"media_cache",remote_descriptor,downloaded)
            assert relay.read_media(receiver,users[1-index].pk,remote_descriptor,decrypt=True)==plain
            cursor[1-index]=page["cursor"]
            manifest["checks"].append(sender+" to "+receiver+" native attachment AEAD, private upload/download, encrypted key delivery")
            print(sender+" -> "+receiver+": native media ciphertext and private HTTP lifecycle passed",flush=True)
        assert Message.objects.count()==4 and Operation.objects.count()==7
        # Private typed Ask responses still use the ordinary authenticated ratchet.
        ask,ask_body,_=send("android",0,content={"type":"card","card":{"type":"ask","question":"Synthetic choice","options":["A","B"]}})
        page=api(users[1],room_path+f"changes/?device_id={devices[1]['id']}&after={cursor[1]}")
        operation=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==ask)
        assert incoming("ios",1,operation)==ask_body
        cursor[1]=page["cursor"]
        vote,vote_body,_=send("ios",1,"card.response",ask,1,{"type":"card_response","reply":{"answer":"","vote":1}})
        page=api(users[0],room_path+f"changes/?device_id={devices[0]['id']}&after={cursor[0]}")
        operation=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==vote)
        assert incoming("android",0,operation)==vote_body
        cursor[0]=page["cursor"]
        manifest["checks"].append("typed Ask question and encrypted partner vote")
        spark,spark_body,_=send("android",0,content={"type":"card","card":{"type":"spark","deck":"Everyday us","question":"What small thing made you smile today?"}})
        page=api(users[1],room_path+f"changes/?device_id={devices[1]['id']}&after={cursor[1]}")
        operation=next(c["operation"] for c in page["changes"] if c.get("operation") and c["operation"]["operation_id"]==spark)
        assert incoming("ios",1,operation)==spark_body
        spark_path=room_path+f"sparks/{spark}/"
        api(users[0],spark_path,{"device_id":devices[0]["id"]})
        held=[]
        for platform,index in (("android",0),("ios",1)):
            claim_id=str(uuid4());peer=devices[1-index]
            bundle=api(users[index],room_path+"bundles/claim/",{"device_id":devices[index]["id"],"recipient_device_id":peer["id"],"operation_id":claim_id})
            command(platform,index,"establish",{"room_id":spark,"claim_id":claim_id,"bundle":bundle})
            answer={"version":1,"spark_id":spark,"answer":f"Synthetic answer {index}"}
            operation={"device_id":devices[index]["id"],"recipient_device_id":peer["id"],"operation_id":str(uuid4()),"target_id":spark,"kind":"card.response","version":1,"reply_to":None}
            value={"room_id":spark,"peer_user_id":str(users[1-index].pk),"peer_identity":peer["identity_key"],"operation":operation,"content":answer}
            encrypted=command(platform,index,"seal",value)
            assert command(platform,index,"seal",value)==encrypted
            held.append((answer,encrypted))
        waiting=api(users[0],spark_path+"answer/",held[0][1])
        assert not waiting["revealed"] and waiting["answers"]==[]
        assert api(users[1],spark_path+f"?device_id={devices[1]['id']}")["answers"]==[]
        api(users[2],spark_path+f"?device_id={devices[1]['id']}",expected=404)
        # Native load is deliberately separate from HTTP quotas. Every engine
        # command closes/reopens its protected store. Ongoing chat's ratchet may
        # advance beyond its skipped-key window without aging a held Spark.
        stress=max(0,min(args.spark_stress_messages,3000))
        commands=[]
        for index in range(stress):
            opid=str(uuid4())
            commands.append({"account":str(users[0].pk),"command":"seal","input":{"room_id":str(room.pk),"peer_user_id":str(users[1].pk),"peer_identity":devices[1]["identity_key"],
                "operation":{"device_id":devices[0]["id"],"recipient_device_id":devices[1]["id"],"operation_id":opid,"target_id":opid,"kind":"message","version":0,"reply_to":None},
                "content":{"version":1,"content":{"type":"text","body":"Synthetic ratchet load","quote":None}}}})
        if commands:
            print(f"Advancing the regular chat by {stress} messages while Spark answers remain held",flush=True)
            sealed=relay.exchange("android",commands);assert all(row.get("ok") for row in sealed)
            commands=[]
            for row in sealed:
                operation={**row["value"],"actor_id":users[0].pk,"sender_device_id":devices[0]["id"]};operation.pop("device_id")
                commands.append({"account":str(users[1].pk),"command":"open","input":{"room_id":str(room.pk),"peer_user_id":str(users[0].pk),"peer_identity":devices[0]["identity_key"],"operation":operation}})
            opened=relay.exchange("ios",commands);assert all(row.get("ok") for row in opened)
        # The stress batch may outlast the normal ten-minute access token.
        # Renew using the actual auth route without changing the device session.
        for user in users:
            request=Request(f"http://127.0.0.1:{server.server_port}/api/auth/refresh/",
                data=json.dumps({"refresh":sessions[user.pk]["refresh"],"request_id":str(uuid4())}).encode(),
                headers={"Content-Type":"application/json"})
            with urlopen(request,timeout=20) as response:
                tokens=json.load(response)["data"]["tokens"]
            sessions[user.pk]=tokens;credentials[user.pk]=tokens["access"]
        released=api(users[1],spark_path+"answer/",held[1][1])
        assert released["revealed"] and len(released["answers"])==2
        assert api(users[1],spark_path+"answer/",held[1][1])==released
        for platform,index in (("android",0),("ios",1)):
            other=1-index;operation=next(row for row in released["answers"] if row["actor_id"]==users[other].pk)
            assert command(platform,index,"open",{"room_id":spark,"peer_user_id":str(users[other].pk),"peer_identity":devices[other]["identity_key"],"operation":operation})==held[other][0]
        manifest["checks"].append(f"Spark mutual release both directions after {stress} regular native ratchet messages and process/store reopen; independent Signal sessions")
        print(f"Spark gate and delayed native reveal passed after {stress} regular messages",flush=True)
        manifest["passed"] = True
        (reports / "result.json").write_text(json.dumps(manifest,indent=2)+"\n")
        print("PASS: production adapters and Django HTTP transport. Evidence: "+str(reports),flush=True)
    finally:
        server.shutdown(); server.server_close(); connections.close_all()


if __name__ == "__main__": main()
