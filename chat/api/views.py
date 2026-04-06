import logging

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.api.serializers import MediaModelSerializer
from chat.models import MediaModel
from utils import image_processing

logger = logging.getLogger(__name__)


class MediaUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    MAX_FILES_PER_REQUEST = 10
    MAX_FILE_SIZE_MB = 15

    def post(self, request, *args, **kwargs):
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

        validated_files = []
        errors = []

        for index, uploaded_file in enumerate(files):
            file_error = self._validate_file(uploaded_file)
            if file_error:
                errors.append({f"file_{index}": file_error})
                continue

            if uploaded_file.content_type and uploaded_file.content_type.startswith("image"):
                uploaded_file = image_processing.process_image(uploaded_file)

            validated_files.append(uploaded_file)

        if errors:
            return Response(
                {
                    "message": "Some files are invalid",
                    "errors": errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            instances = [
                MediaModel.objects.create(file=uploaded_file)
                for uploaded_file in validated_files
            ]

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