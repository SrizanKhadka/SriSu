from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser
from rest_framework.response import Response
from .api import ChatView
from . import media, serializers


class CiphertextParser(BaseParser):
    media_type="application/octet-stream"

    def parse(self,stream,media_type=None,parser_context=None):
        value=stream.read(media.MAX_CIPHERTEXT+1)
        if len(value)>media.MAX_CIPHERTEXT:
            raise ParseError("Encrypted files are limited to 16 MiB plus encryption overhead.")
        return value


class AttachmentInitView(ChatView):
    def post(self,request,room_id):
        return Response({"data":media.initialize(request.user,request.auth["sid"],room_id,self.validated(request,serializers.AttachmentInit))})


class AttachmentUploadView(ChatView):
    parser_classes=[CiphertextParser]

    def put(self,request,room_id,attachment_id):
        device=serializers.AttachmentDevice(data={"device_id":request.headers.get("X-Chat-Device")})
        device.is_valid(raise_exception=True)
        # Reject unrelated accounts before reading an upload into bounded memory.
        from .services import room_for
        room_for(request.user,room_id)
        return Response({"data":media.upload(request.user,request.auth["sid"],room_id,attachment_id,
            device.validated_data["device_id"],request.data)})


class AttachmentFinalizeView(ChatView):
    def post(self,request,room_id,attachment_id):
        value=self.validated(request,serializers.AttachmentDevice)
        return Response({"data":media.finalize(request.user,request.auth["sid"],room_id,attachment_id,value["device_id"])})


class AttachmentCancelView(ChatView):
    def post(self,request,room_id,attachment_id):
        value=self.validated(request,serializers.AttachmentDevice)
        return Response({"data":media.cancel(request.user,request.auth["sid"],room_id,attachment_id,value["device_id"])})


class AttachmentCapabilityView(ChatView):
    def post(self,request,room_id,attachment_id):
        value=self.validated(request,serializers.AttachmentDevice)
        return Response({"data":media.capability(request.user,request.auth["sid"],room_id,attachment_id,value["device_id"])})


class AttachmentDownloadView(ChatView):
    def get(self,request,room_id,attachment_id):
        token=request.headers.get("X-Chat-Media-Capability","")
        if len(token)>2048: raise ParseError("Invalid media capability.")
        return media.download(request.user,request.auth["sid"],room_id,attachment_id,token)
