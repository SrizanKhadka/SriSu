from rest_framework.viewsets import ModelViewSet
from rest_framework import permissions
from chat.api.serializers import *
from chat.models import *
from chat.utils.chatutils import *
class MediaUploadView(ModelViewSet):
    serializer_class = MediaModelSerializer
    queryset = MediaModel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
