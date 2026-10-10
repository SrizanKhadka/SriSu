"""Real Android KMP repositories against an isolated, disposable PostgreSQL/ASGI app.

Uses only the dedicated test Docker PostgreSQL port 55449. No app database,
production credential, external service, installed account or user media is used.
Build/install the current main debug app and instrumentation APK before running.
"""
import argparse
import ipaddress
import shlex
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def serve(args):
    assert re.fullmatch(r"cc_app_[0-9a-f]{32}",args.database)
    sys.path.insert(0,str(ROOT))
    os.environ["DJANGO_SETTINGS_MODULE"]="srisu.workspace_test_settings"
    from django.conf import settings
    settings.DATABASES={"default":{"ENGINE":"django.db.backends.postgresql","NAME":args.database,
        "USER":"postgres","HOST":"127.0.0.1","PORT":"55449"}}
    settings.ALLOWED_HOSTS=["127.0.0.1","localhost",args.bind]
    settings.COUPLE_CHAT_PREVIEW_ENABLED=True
    settings.MEDIA_ROOT=str(args.fixture.parent/"media")
    settings.LOGGING["loggers"]["srisu"]["level"]="ERROR"
    original=socket.socket.connect
    def local_only(sock,address):
        if isinstance(address,tuple) and address[0] not in ("127.0.0.1","::1","localhost"):
            raise RuntimeError("External services disabled in chat integration")
        return original(sock,address)
    socket.socket.connect=local_only
    import django
    django.setup()
    from django.core.management import call_command
    from authentication.models import UserModel
    from authentication.sessions import create_session
    from social.services.relationship_service import invite,transition
    from utils.choices import CoupleConnectionStatus as Status
    with redirect_stdout(io.StringIO()):call_command("migrate",verbosity=0,interactive=False)
    users=[UserModel.objects.create_user(id=810000000000000001+i,phone_number=f"+977980091000{i}",
        full_name=f"Synthetic app participant {i}",is_phone_verified=True,is_profile_complete=True) for i in range(3)]
    invitation,_=invite(users[0],users[1].phone_number)
    room=transition(users[1],invitation.pk,Status.ACCEPTED,users[0].phone_number,users[1].phone_number)[2]
    payload={"base_url":f"http://{args.bind}:{args.port}/","room_id":str(room.pk),
        "users":[{"id":u.pk,**create_session(u)} for u in users]}
    descriptor=os.open(args.fixture,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,"w") as stream:json.dump(payload,stream)
    from srisu.asgi import application
    from daphne.server import Server
    Server(application=application,endpoints=[f"tcp:port={args.port}:interface={args.bind}"],signal_handlers=False,verbosity=0).run()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb");parser.add_argument("--serial",default="emulator-5580")
    parser.add_argument("--ui",action="store_true",help="Exercise the visible Compose conversation with the real repositories")
    parser.add_argument("--ios",action="store_true")
    parser.add_argument("--bind",default="127.0.0.1")
    parser.add_argument("--mac-runner",type=Path)
    parser.add_argument("--mac-root",default="/Users/srijan/srisu-couple-chat-evaluation")
    parser.add_argument("--serve",action="store_true");parser.add_argument("--database")
    parser.add_argument("--fixture",type=Path);parser.add_argument("--port",type=int)
    args=parser.parse_args()
    if args.serve:return serve(args)
    address=ipaddress.ip_address(args.bind)
    if not address.is_private or address.is_unspecified:parser.error("Use a specific private test interface")
    if args.ios:
        if not args.mac_runner:parser.error("--ios requires the strictly host-verified --mac-runner")
    elif not args.adb:parser.error("--adb is required")
    import psycopg2
    from psycopg2 import sql
    database="cc_app_"+uuid4().hex
    report=ROOT/".cache/chat-app-integration"/database
    report.mkdir(parents=True)
    fixture=report/"fixture.json"
    admin=psycopg2.connect(host="127.0.0.1",port=55449,user="postgres",dbname="postgres");admin.autocommit=True
    with admin.cursor() as cursor:cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    with socket.socket() as port_socket:port_socket.bind(("127.0.0.1",0));port=port_socket.getsockname()[1]
    def adb(*arguments,input=None,timeout=120):
        result=subprocess.run([args.adb,"-s",args.serial,*arguments],input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        if result.returncode:raise RuntimeError("Android integration device command failed")
        return result.stdout
    server=None
    remote_fixture=args.mac_root+"/ios-chat-fixture.json"
    def mac(script,timeout=600):
        result=subprocess.run([sys.executable,str(args.mac_runner)],input=script.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        if result.returncode:
            (report/"ios.log").write_bytes(result.stdout+result.stderr)
            raise RuntimeError("Mac app integration failed; inspect "+str(report/"ios.log"))
        return result.stdout
    try:
        with (report/"server.log").open("w") as log:
            server=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--serve","--database",database,"--fixture",str(fixture),"--port",str(port),"--bind",args.bind],
                cwd=ROOT,stdout=log,stderr=log)
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if server.poll() is not None:raise RuntimeError("Disposable chat backend stopped; inspect server.log")
                if fixture.is_file():
                    try:
                        with socket.create_connection((args.bind,port),timeout=1):break
                    except OSError:pass
                time.sleep(.2)
            else:raise RuntimeError("Disposable chat backend did not start")
            if args.ios:
                subprocess.run([sys.executable,str(args.mac_runner),"upload",str(fixture),remote_fixture],check=True,timeout=120)
                script="\n".join([
                    "set -euo pipefail",
                    "data=$(xcrun simctl get_app_container 0055683D-7DBA-44C7-855D-4AA64123C522 com.srisu.srisu.SriSu data)",
                    "cp "+shlex.quote(remote_fixture)+' "$data/Documents/chat-app-fixture.json"',
                    "rm -f "+shlex.quote(remote_fixture),
                    "cd "+shlex.quote(args.mac_root+"/frontend/iosApp"),
                    "test_status=0",
                    "xcodebuild test-without-building -workspace iosApp.xcworkspace -scheme SriSuChatTests -destination 'platform=iOS Simulator,id=0055683D-7DBA-44C7-855D-4AA64123C522' -derivedDataPath build/chat-app-evaluation -only-testing:SriSuChatTests/ChatMediaTests/testActualKmpRepositoriesWithTwoIosPartners > ios-app-integration.log 2>&1 || test_status=$?",
                    "tail -25 ios-app-integration.log",
                    "exit $test_status",
                ])
                print("Running two iOS partners through the actual KMP repositories and disposable PostgreSQL",flush=True)
                output=mac(script)
                (report/"ios.log").write_bytes(output)
                if b"Executed 1 test, with 0 failures" not in output:raise RuntimeError("iOS did not execute the expected app test")
                (report/"result.json").write_text(json.dumps({"passed":True,"scope":"iOS KMP repositories, native Signal/Keychain/protected SQLite, PostgreSQL HTTP/WebSockets","compose_ui":False},indent=2)+"\n")
                print("PASS. Evidence: "+str(report),flush=True)
                return
            adb("reverse",f"tcp:{port}",f"tcp:{port}")
            adb("shell","run-as","com.srisu.srisu","mkdir","-p","files")
            adb("shell","-T","run-as com.srisu.srisu sh -c 'cat > files/chat-app-fixture.json'",input=fixture.read_bytes())
            print("Running two synthetic partners through the actual KMP chat "+("UI" if args.ui else "repositories"),flush=True)
            test_class="com.srisu.srisu.features.couplechat."+("ChatAppUiIntegrationTest" if args.ui else "ChatAppIntegrationTest")
            output=adb("shell","am","instrument","-w","-r","-e","class",test_class,
                "com.srisu.srisu.test/androidx.test.runner.AndroidJUnitRunner",timeout=600)
            (report/"android.log").write_bytes(output)
            if b"OK (1 test)" not in output or b"FAILURES!!!" in output:raise RuntimeError("App integration failed; inspect "+str(report/"android.log"))
            (report/"result.json").write_text(json.dumps({"passed":True,"scope":"Android KMP repositories, native libsignal/SQLite/Keystore, real PostgreSQL HTTP and WebSocket", "compose_ui":args.ui},indent=2)+"\n")
            print("PASS. Evidence: "+str(report),flush=True)
    finally:
        with admin.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s",(database,))
            if cursor.fetchone():
                try:
                    evidence=psycopg2.connect(host="127.0.0.1",port=55449,user="postgres",dbname=database)
                    with evidence.cursor() as sample:
                        sample.execute("SELECT kind, actor_id, count(*) FROM couple_chat_operation GROUP BY kind, actor_id ORDER BY kind, actor_id")
                        (report/"operation-counts.json").write_text(json.dumps(sample.fetchall())+"\n")
                    evidence.close()
                except psycopg2.Error:pass # The server may have failed before migrations.
        if args.ios:
            try:mac("\n".join(["rm -f "+shlex.quote(remote_fixture),
                "data=$(xcrun simctl get_app_container 0055683D-7DBA-44C7-855D-4AA64123C522 com.srisu.srisu.SriSu data)",
                'if [ -n "$data" ]; then rm -f "$data/Documents/chat-app-fixture.json"; fi']),timeout=20)
            except (RuntimeError,subprocess.TimeoutExpired):pass
        else:
            try:adb("shell","run-as","com.srisu.srisu","rm","-f","files/chat-app-fixture.json")
            except (RuntimeError,subprocess.TimeoutExpired):pass
            try:adb("reverse","--remove",f"tcp:{port}")
            except (RuntimeError,subprocess.TimeoutExpired):pass
        if server is not None:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        fixture.unlink(missing_ok=True)
        with admin.cursor() as cursor:cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
        admin.close()


if __name__=="__main__":main()
