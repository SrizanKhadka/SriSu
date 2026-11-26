from django.db.models import Prefetch
from rest_framework import serializers
from rest_framework.throttling import UserRateThrottle
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from social.api.couple_views import CoupleAPI, envelope
from social.api.couple_pagination import positive_int, decode_cursor, keyset, next_url, page_size, SessionChanged
from social.api.couple_profile_serializers import StrictSerializer
from social.models import CouplePublicFollow, CoupleMembershipModel
from social.services.couple_profile_sections import profile_for, audience
from social.services.public_follow_service import snapshot, follow, unfollow, incoming_rows


class PublicFollowThrottle(UserRateThrottle):
    scope = 'public_follows'
    rate = '120/min'


class PublicFollowInput(StrictSerializer):
    share_identity = serializers.BooleanField()

    def validate_share_identity(self, value):
        if value is not True or self.initial_data.get('share_identity') is not True:
            raise serializers.ValidationError('Confirm sharing your public identity with this couple to follow.')
        return value


class CoupleFollowDetail(CoupleAPI):
    throttle_classes = [PublicFollowThrottle]

    def expected(self, request):
        value = request.headers.get('If-Match')
        if value is None:
            return None
        if not value.isascii() or not value.isdecimal() or len(value) > 18:
            raise ValidationError({'If-Match': 'Use the current nonnegative following version.'})
        return int(value)

    def get(self, request, couple_id):
        return Response({'data': snapshot(request.user, positive_int(couple_id, 'couple'))})

    def put(self, request, couple_id):
        data = PublicFollowInput(data=request.data); data.is_valid(raise_exception=True)
        target = positive_int(couple_id, 'couple')
        created = follow(request.user, target, self.expected(request))
        return Response({'data': snapshot(request.user, target)}, status=201 if created else 200)

    def delete(self, request, couple_id):
        if request.data:
            raise ValidationError({'body': 'No body is accepted.'})
        target = positive_int(couple_id, 'couple')
        unfollow(request.user, target, self.expected(request))
        return Response({'data': snapshot(request.user, target)})


class CoupleFollowerList(CoupleAPI):
    throttle_classes = [PublicFollowThrottle]

    def get(self, request):
        if 'couple_id' in request.query_params or 'couple' in request.query_params:
            raise ValidationError({'couple': 'Only your current couple has an incoming follower list.'})
        if not CoupleMembershipModel.objects.filter(user=request.user).exists():
            return envelope([], couple_id=None)
        target, members, _ = profile_for(request.user, owner=True)
        state = decode_cursor(request, 'public-followers')
        if state and (state['couple'] != target.pk or state['audience'] != audience(members)):
            raise SessionChanged()
        size = page_size(request, state)
        people = CoupleMembershipModel.objects.select_related('user')
        rows = CouplePublicFollow.objects.filter(couple=target).select_related('source_membership__couple').prefetch_related(
            Prefetch('source_membership__couple__memberships', queryset=people), 'source_membership__couple__privacy_consents')
        rows = list(keyset(rows, state).order_by('-created_at', '-id')[:size + 1])
        page = rows[:size]
        link = next_url(request, 'public-followers', {'couple': target.pk, 'audience': audience(members), 'size': size,
            'last_at': page[-1].created_at.isoformat(), 'last_id': page[-1].pk}) if len(rows) > size else None
        return envelope(incoming_rows(request, target, members, page), link, couple_id=target.pk)
