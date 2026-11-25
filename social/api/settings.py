"""Authenticated Settings projection; no partner-wide notification mutations."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import APIException, NotFound
from rest_framework.response import Response
from rest_framework.views import APIView
from social.api.couple_profile_serializers import StrictSerializer
from social.api.moment_views import PrivateResponseMixin
from social.models import CoupleConnectionModel, CoupleMembershipModel
from social.services.relationship_service import end_connection, RelationshipConflict, RelationshipPermissionDenied
from utils.choices import CoupleConnectionStatus


class SettingsConflict(APIException):
    status_code = 409
    default_detail = "Your connection changed. Reload Settings and try again."


class DisconnectInput(StrictSerializer):
    connection_id = serializers.IntegerField(min_value=1)
    expected_revision = serializers.IntegerField(min_value=1)


class SettingsContext(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = (CoupleMembershipModel.objects.select_related("couple__couple_connection")
            .filter(user=request.user, ended_at__isnull=True,
                    couple__couple_connection__connection_status=CoupleConnectionStatus.ACCEPTED)
            .order_by("-id").first())
        partner = None
        if membership:
            members = list(CoupleMembershipModel.objects.select_related("user")
                .filter(couple=membership.couple, ended_at__isnull=True)
                .order_by("id")[:3])
            connection = membership.couple.couple_connection
            # Membership alone must not expose an unrelated connection if legacy data
            # is inconsistent. Match both active members against its actual participants.
            if (len(members) != 2 or any(not member.user.is_active for member in members)
                    or {member.user.phone_number for member in members}
                    != {connection.sender_number, connection.receiver_number}
                    or request.user.pk not in {member.user_id for member in members}):
                raise SettingsConflict()
            other = next(member for member in members if member.user_id != request.user.pk)
            person = other.user
            since = connection.accepted_at
            partner = {
                "user_id": person.pk, "name": person.full_name or person.username or "Your partner",
                "photo": request.build_absolute_uri(person.profile_photo.url) if person.profile_photo else None,
                "couple_id": membership.couple_id, "connection_id": connection.pk,
                "revision": connection.revision,
                "connected_days": max(0, (timezone.now() - since).days) if since else None,
            }
        return Response({"data": {"contract": "settings-1", "partner": partner}})


class SettingsDisconnect(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        value = DisconnectInput(data=request.data)
        value.is_valid(raise_exception=True)
        fields = value.validated_data
        if not CoupleConnectionModel.objects.filter(pk=fields["connection_id"]).filter(
            Q(sender_number=request.user.phone_number) | Q(receiver_number=request.user.phone_number)
        ).exists():
            raise NotFound()
        try:
            end_connection(connection_id=fields["connection_id"], actor=request.user,
                           expected_revision=fields["expected_revision"])
        except RelationshipPermissionDenied:
            raise NotFound()
        except RelationshipConflict:
            raise SettingsConflict()
        return SettingsContext().get(request)
