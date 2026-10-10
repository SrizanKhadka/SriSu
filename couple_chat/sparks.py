"""Honest-server reveal gating of independent, library-encrypted sessions.

An answer's Signal address uses the Spark UUID, never the ongoing chat address.
At most one immutable answer from each device is accepted. No held ciphertext
is put in regular history, socket hints, or a response before both submissions.
This is release authorization, not cryptographic fair exchange against the server.
"""
import json
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from .api import ChatView
from .models import Spark, SparkAnswer, Message
from .protocol import lock_room, partner_device, digest, Conflict
from .services import append_change
from .serializers import AttachmentDevice, Envelope


def locked(user, sid, room_id, spark_id, device_id):
    room, device = lock_room(user, sid, room_id, device_id)
    message = Message.objects.filter(pk=spark_id, room=room, deleted_at__isnull=True).first()
    if message is None:
        raise NotFound()
    spark = Spark.objects.select_related("first_device", "second_device").filter(message=message).first()
    if spark is None:
        raise NotFound()
    devices = {spark.first_device.user_id:spark.first_device, spark.second_device.user_id:spark.second_device}
    if devices[user.pk].pk != device.pk:
        raise Conflict("spark_device_changed", "Start a new Spark after replacing a chat device.")
    other = next(value for key,value in devices.items() if key != user.pk)
    partner_device(user, room, other.pk)
    return room, spark, device, other


def status(spark, actor):
    rows = list(spark.answers.order_by("actor_id"))
    revealed = spark.revealed_at is not None and len(rows) == 2
    result = {"id":str(spark.pk), "submitted":any(row.actor_id==actor.pk for row in rows),
        "partner_submitted":any(row.actor_id!=actor.pk for row in rows), "revealed":revealed, "answers":[]}
    if revealed:
        result["answers"] = [{"actor_id":row.actor_id, "sender_device_id":row.envelope["device_id"],
            **{key:value for key,value in row.envelope.items() if key!="device_id"}, "applied":True,
            "unavailable":False, "target_deleted":False} for row in rows]
    return result


class SparkView(ChatView):
    @transaction.atomic
    def post(self, request, room_id, spark_id):
        data=self.validated(request,AttachmentDevice)
        room,device=lock_room(request.user,request.auth["sid"],room_id,data["device_id"])
        message=Message.objects.filter(pk=spark_id,room=room,author=request.user,deleted_at__isnull=True).first()
        if message is None: raise NotFound()
        spark=Spark.objects.filter(message=message).first()
        if spark is None:
            if Spark.objects.filter(message__room=room,message__deleted_at__isnull=True,revealed_at__isnull=True).count()>=2:
                raise Conflict("spark_limit","Finish or delete an open Spark before starting another.")
            from .protocol import live_devices
            other_id=room.second_id if room.first_id==request.user.pk else room.first_id
            other=live_devices().filter(user_id=other_id).first()
            if other is None: raise Conflict("partner_device_changed","Your partner needs to open chat.")
            devices=sorted([device,other],key=lambda value:value.user_id)
            spark=Spark.objects.create(message=message,first_device=devices[0],second_device=devices[1])
            append_change(room,"spark.changed",{"message_id":str(spark_id)})
        # Recheck even an idempotent init after device replacement.
        locked(request.user,request.auth["sid"],room_id,spark_id,data["device_id"])
        return Response({"data":status(spark,request.user)})

    @transaction.atomic
    def get(self,request,room_id,spark_id):
        query=AttachmentDevice(data=request.query_params);query.is_valid(raise_exception=True)
        _,spark,_,_=locked(request.user,request.auth["sid"],room_id,spark_id,query.validated_data["device_id"])
        return Response({"data":status(spark,request.user)})


class SparkAnswerView(ChatView):
    @transaction.atomic
    def post(self,request,room_id,spark_id):
        data=self.validated(request,Envelope)
        room,spark,device,other=locked(request.user,request.auth["sid"],room_id,spark_id,data["device_id"])
        if data["kind"]!="card.response" or data["target_id"]!=spark_id or data["version"]!=1 or data["recipient_device_id"]!=other.pk:
            raise Conflict("invalid_spark_answer","The answer must match this Spark and both original devices.")
        fingerprint=digest(data)
        existing=SparkAnswer.objects.filter(spark=spark,actor=request.user).first()
        if existing:
            if existing.digest!=fingerprint:raise Conflict("operation_conflict","A submitted answer cannot change.")
        else:
            # JSON contains routing/public identifiers and opaque ciphertext only.
            SparkAnswer.objects.create(spark=spark,actor=request.user,operation_id=data["operation_id"],
                envelope=json.loads(json.dumps(data,default=str)),digest=fingerprint)
            if spark.answers.count()==2:
                spark.revealed_at=timezone.now();spark.save(update_fields=["revealed_at"])
            append_change(room,"spark.changed",{"message_id":str(spark_id)})
        return Response({"data":status(spark,request.user)})
