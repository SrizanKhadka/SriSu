import logging

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from chat.api.serializers import MediaModelSerializer
from chat.models import MediaModel
from chat.selectors.chat_room_selectors import get_chat_room_for_user
from chat.services.authorization import lock_authorized_room, lock_current_device_session
from utils import image_processing

logger = logging.getLogger(__name__)


class MediaUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat_media_uploads"

    MAX_FILES_PER_REQUEST = 10
    MAX_FILE_SIZE_MB = 15
    MAX_TOTAL_SIZE_MB = 25

    def post(self, request, *args, **kwargs):
        room_id = request.data.get("chat_room_id")
        room = get_chat_room_for_user(room_id, request.user) if room_id else None
        if room_id and room is None:
            return Response(
                {"message": "Chat room not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if room is not None and room.encrypted_v2_started_at is not None:
            return Response(
                {"message": "This room requires the chat v2 ciphertext protocol."},
                status=status.HTTP_409_CONFLICT,
            )
        files = request.FILES.getlist("file")

        if not files:
            return Response(
                {
                    "message": "No files provided",
                    "errors": {"file": ["At least one file is required."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(files) > self.MAX_FILES_PER_REQUEST:
            return Response(
                {
                    "message": "Too many files",
                    "errors": {
                        "file": [
                            f"You can upload at most {self.MAX_FILES_PER_REQUEST} files at once."
                        ]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sum(uploaded_file.size for uploaded_file in files) > self.MAX_TOTAL_SIZE_MB * 1024 * 1024:
            return Response(
                {
                    "message": "Upload is too large",
                    "errors": {
                        "file": [
                            f"Combined file size must not exceed {self.MAX_TOTAL_SIZE_MB} MB."
                        ]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_files = []
        errors = []

        for index, uploaded_file in enumerate(files):
            file_error = self._validate_file(uploaded_file)
            if file_error:
                errors.append({f"file_{index}": file_error})
                continue

            try:
                uploaded_file = image_processing.process_image(uploaded_file)
            except image_processing.InvalidImage as exc:
                errors.append({f"file_{index}": str(exc)})
                continue

            validated_files.append(uploaded_file)

        if errors:
            return Response(
                {
                    "message": "Some files are invalid",
                    "errors": errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        instances = []
        stored_names = []
        storage = MediaModel._meta.get_field("file").storage
        try:
            with transaction.atomic():
                if room is not None:
                    room = lock_authorized_room(room.pk, request.user)
                    if room is None:
                        return Response(
                            {"message": "Chat room not found."},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    if room.encrypted_v2_started_at is not None:
                        return Response(
                            {"message": "This room requires the chat v2 ciphertext protocol."},
                            status=status.HTTP_409_CONFLICT,
                        )
                auth = getattr(request, "auth", None)
                session_id = auth.get("sid") if hasattr(auth, "get") else None
                if session_id and lock_current_device_session(request.user, session_id) is None:
                    return Response(
                        {"message": "Session expired."},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                for uploaded_file in validated_files:
                    instance = MediaModel.objects.create(
                        file=uploaded_file,
                        owner=request.user,
                        chat_room=room,
                    )
                    instances.append(instance)
                    stored_names.append(instance.file.name)
        except Exception:
            for name in stored_names:
                try:
                    storage.delete(name)
                except Exception:
                    logger.warning("legacy_media_cleanup_failed")
            raise

        serializer = MediaModelSerializer(
            instances,
            many=True,
            context={"request": request},
        )

        return Response(
            {
                "message": "Media uploaded successfully",
                "data": {
                    "media": serializer.data,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    def _validate_file(self, uploaded_file):
        if not uploaded_file:
            return "Invalid file."

        size_mb = uploaded_file.size / (1024 * 1024)
        if size_mb > self.MAX_FILE_SIZE_MB:
            return f"File size must not exceed {self.MAX_FILE_SIZE_MB} MB."

        return None
