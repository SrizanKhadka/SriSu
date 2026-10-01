"""Non-mutating compatibility response for discontinued dating routes."""
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import APIException

class DatingRetired(APIException):
    status_code = 410
    default_detail = "Dating features have been retired. Update SriSu to continue using couple features."
    default_code = "feature_retired"
    core_error_code = "feature_retired"

class RetiredDatingView(APIView):
    permission_classes = [IsAuthenticated]
    def retired(self, request, *args, **kwargs):
        raise DatingRetired()
    get = post = put = patch = delete = head = retired
