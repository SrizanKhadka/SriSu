from rest_framework.viewsets import ModelViewSet
from rest_framework import permissions
from chat.api.serializers import *
from chat.models import *
from chat.utils.chatutils import *
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from utils import image_processing

class MediaUploadView(ModelViewSet):
    serializer_class = MediaModelSerializer
    queryset = MediaModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    
    def create(self, request, *args, **kwargs):
        files = request.FILES.getlist('file')
        instances = []

        for f in files:
            if f.content_type.startswith("image"):
                f = image_processing.process_image(f)
                size_mb = f.size / (1024 * 1024)
                print(f"Processed image size: {size_mb:.2f} MB")    

            instance = MediaModel.objects.create(file=f)
            instances.append(instance)

        serializer = self.get_serializer(instances, many=True)

        return Response({
            "message": "Media uploaded successfully",
            "data": {"media": serializer.data}
        })


