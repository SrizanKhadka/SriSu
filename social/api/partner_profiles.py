from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from social.api.views import PartnerDiscoveryThrottle
from social.services.partner_profile_service import partner_profile, serialize_partner_profile


class PartnerProfileDetail(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [PartnerDiscoveryThrottle]
    http_method_names = ["get", "head", "options"]

    def get(self, request, user_id):
        profile = partner_profile(request.user, user_id, request.headers.get("X-Partner-Profile-Access"))
        response = Response({"data": serialize_partner_profile(request, *profile)})
        response["Cache-Control"] = "private, no-store"
        return response
