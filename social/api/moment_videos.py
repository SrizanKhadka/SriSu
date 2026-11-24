import asyncio
import re
import time

from django.core import signing
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle

from authentication.models import UserModel, DeviceSession
from authentication.sessions import SessionJWTAuthentication
from social.models import MomentDraftPhoto, CoupleMomentPhotoModel, MomentVideo
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.api.moment_drafts import DraftThrottle
from social.services.moment_publication import draft_for
from social.services.moment_service import eligible_moments
from social.services.moment_video import (begin_video, owned_video, upload_chunk, finish_video,
    retry_video, video_data, MAX_BYTES, CHUNK_BYTES)

COOKIE = "srisu_moment_video"
SALT = "srisu.moment.video.v1"


class BeginVideo(StrictSerializer):
    source_bytes = serializers.IntegerField(min_value=1, max_value=MAX_BYTES)


class VideoStatusThrottle(UserRateThrottle):
    scope = "moment_video_status"
    rate = "120/minute"


class VideoUpload(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [DraftThrottle]

    def get_throttles(self):
        # Processing polls must not spend the hourly draft-write allowance.
        return [VideoStatusThrottle()] if self.request.method == "GET" else super().get_throttles()

    def post(self, request, draft_id, video_id):
        data = BeginVideo(data=request.data); data.is_valid(raise_exception=True)
        video = begin_video(request.user, draft_id, video_id, data.validated_data["source_bytes"])
        return Response({"data": video_data(video)})

    def get(self, request, draft_id, video_id):
        _, video = owned_video(request.user, draft_id, video_id)
        return Response({"data": video_data(video)})


class VideoChunkBurstThrottle(UserRateThrottle):
    scope = "moment_video_chunk_burst"
    rate = "360/minute"


class VideoChunkHourlyThrottle(UserRateThrottle):
    scope = "moment_video_chunk_hour"
    rate = "1000/hour"


class VideoChunkUpload(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    # Includes replays and rejected attempts; each body is capped at 2 MiB.
    throttle_classes = [VideoChunkBurstThrottle, VideoChunkHourlyThrottle]
    parser_classes = []

    def put(self, request, draft_id, video_id, offset):
        owned_video(request.user, draft_id, video_id)  # Authorize before consuming a body.
        try:
            size = int(request.META.get("CONTENT_LENGTH", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= CHUNK_BYTES or request.content_type != "application/octet-stream":
            return Response({"message": "Upload chunks must be at most 2 MiB."}, status=413)
        raw = request._request.read(CHUNK_BYTES + 1)
        if len(raw) != size:
            raise ValidationError({"chunk": "The upload chunk is incomplete."})
        video = upload_chunk(request.user, draft_id, video_id, offset, ContentFile(raw))
        return Response({"data": video_data(video)})


class VideoComplete(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [DraftThrottle]

    def post(self, request, draft_id, video_id):
        return Response({"data": video_data(finish_video(request.user, draft_id, video_id))})


class VideoRetry(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [DraftThrottle]

    def post(self, request, draft_id, video_id):
        return Response({"data": video_data(retry_video(request.user, draft_id, video_id))})


def authorized_video(user, video_id):
    video = MomentVideo.objects.filter(pk=video_id, state="ready").first()
    if video is None:
        raise Http404
    staged = MomentDraftPhoto.objects.filter(video=video, draft__owner=user).first()
    if staged:
        draft_for(user, staged.draft_id)
        return video
    if CoupleMomentPhotoModel.objects.filter(video=video, moment__in=eligible_moments(user)).exists():
        return video
    raise Http404


class VideoPlayback(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, video_id):
        video = authorized_video(request.user, video_id)
        auth = request.auth or {}
        expiry = min(int(time.time()) + 120, int(auth.get("exp", int(time.time()) + 120)))
        cookie = signing.dumps({"uid": request.user.pk, "vid": str(video.pk),
            "sid": auth.get("sid"), "exp": expiry}, salt=SALT)
        path = f"/api/social/moment-video-stream/{video.pk}/"
        return Response({"data": {"url": request.build_absolute_uri(path + "master.m3u8"),
            "cookie_name": COOKIE, "cookie_value": cookie, "cookie_path": path,
            "expires_in": max(0, expiry - int(time.time())), "duration_ms": video.duration_ms}})


class VideoCookieAuthentication(BaseAuthentication):
    def authenticate(self, request):
        cookie = request.COOKIES.get(COOKIE)
        if not cookie:
            return None
        try:
            payload = signing.loads(cookie, salt=SALT, max_age=120)
            if payload["exp"] <= time.time() or str(request.parser_context["kwargs"]["video_id"]) != payload["vid"]:
                raise ValueError()
            user = UserModel.objects.get(pk=payload["uid"], is_active=True)
            if payload.get("sid") and not DeviceSession.objects.filter(pk=payload["sid"], user=user,
                    revoked_at__isnull=True, expires_at__gt=timezone.now()).exists():
                raise ValueError()
            return user, payload
        except (signing.BadSignature, ValueError, KeyError, UserModel.DoesNotExist):
            raise AuthenticationFailed("Video access expired. Open the Moment again.")

    def authenticate_header(self, request):
        return "Bearer"


class VideoStream(PrivateResponseMixin, APIView):
    def head(self, request, video_id, name):
        return self.get(request, video_id, name)

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [SessionJWTAuthentication, VideoCookieAuthentication]

    def get(self, request, video_id, name):
        video = authorized_video(request.user, video_id)
        # Input chunks are never publicly addressable, even by their owner.
        if not re.fullmatch(r"(?:master|360|720)\.m3u8|(?:360|720)_[0-9]{3}\.ts|poster\.jpg", name):
            raise Http404
        file = video.files.filter(name=name).first()
        if file is None:
            raise Http404
        if not file.file.storage.exists(file.file.name):
            raise Http404
        start, end, status = 0, file.size - 1, 200
        value = request.headers.get("Range")
        if value:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
            try:
                if not match or not any(match.groups()):
                    raise ValueError()
                left, right = match.groups()
                if left:
                    start = int(left); end = min(int(right), end) if right else end
                else:
                    length = int(right)
                    if length <= 0: raise ValueError()
                    start = max(0, file.size - length)
                if start > end or start >= file.size:
                    raise ValueError()
            except ValueError:
                response = HttpResponse(status=416); response["Content-Range"] = f"bytes */{file.size}"
                return response
            status = 206
        content_type = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t" if name.endswith(".ts") else "image/jpeg"
        length = end - start + 1
        def blocks():
            with file.file.open("rb") as source:
                source.seek(start); remaining = length
                while remaining:
                    data = source.read(min(65536, remaining))
                    if not data: break
                    remaining -= len(data); yield data
        async def async_blocks():
            iterator = blocks()
            try:
                while True:
                    data = await asyncio.to_thread(next, iterator, None)
                    if data is None: break
                    yield data
            finally:
                await asyncio.to_thread(iterator.close)
        if request.method == "HEAD":
            response = HttpResponse(status=status, content_type=content_type)
        else:
            response = StreamingHttpResponse(async_blocks() if hasattr(request._request, "scope") else blocks(), status=status, content_type=content_type)
        response["Content-Length"] = str(length)
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        if status == 206:
            response["Content-Range"] = f"bytes {start}-{end}/{file.size}"
        return response
