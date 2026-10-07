"""Recoverable HTTP reads using the same authorized selectors and socket DTOs."""
from uuid import UUID

from django.core import signing
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from chat.selectors.access import authorized_rooms

from chat.presenters.chat_room_presenter import serialize_chat_room_preview_for_socket_sync
from chat.presenters.message_presenter import serialize_message_for_socket_sync
from chat.selectors.chat_room_selectors import get_chat_room_for_user, get_user_chat_rooms_queryset
from chat.selectors.message_selectors import get_paginated_messages_before


class HistoryQuery(serializers.Serializer):
    limit = serializers.IntegerField(default=20, min_value=1, max_value=50)
    cursor = serializers.IntegerField(required=False, min_value=1)


class RoomQuery(serializers.Serializer):
    limit = serializers.IntegerField(default=20, min_value=1, max_value=50)
    cursor = serializers.CharField(required=False, max_length=4096)


def request_scope(request):
    return {
        'scheme': request.scheme,
        'headers': [(b'host', request.get_host().encode())],
        'user': request.user,
    }


class RoomHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat_reads"

    def get(self, request, room_id):
        query = HistoryQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        room = get_chat_room_for_user(room_id, request.user)
        if room is None:
            raise NotFound()
        messages, has_more, cursor = get_paginated_messages_before(room, request.user, **query.validated_data)
        data = {'chat_room_id': str(room.pk),
                'messages': [serialize_message_for_socket_sync(m, request_scope(request)) for m in messages],
                'has_more': has_more, 'next_cursor': cursor}
        if get_chat_room_for_user(room_id, request.user) is None:
            raise NotFound()
        response = Response({'message': 'Messages fetched successfully', 'data': data})
        response['Cache-Control'] = 'private, no-store'
        return response


class RoomListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat_reads"
    cursor_salt = 'srisu.chat.rooms.core-1'

    def get(self, request):
        query = RoomQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        limit = query.validated_data['limit']
        queryset = get_user_chat_rooms_queryset(request.user)
        cursor = query.validated_data.get('cursor')
        if cursor:
            try:
                state = signing.loads(cursor, salt=self.cursor_salt, max_age=900)
                at, room_id = parse_datetime(state['at']), UUID(state['id'])
                if state['user'] != request.user.pk or at is None:
                    raise ValueError()
            except (signing.BadSignature, ValueError, KeyError, TypeError):
                raise serializers.ValidationError({'cursor': 'invalid_cursor'})
            queryset = queryset.filter(Q(updated_at__lt=at) | Q(updated_at=at, id__lt=room_id))
        rooms = list(queryset[:limit + 1])
        allowed = set(authorized_rooms(request.user).filter(pk__in=[room.pk for room in rooms[:limit]]).values_list("pk", flat=True))
        data = []
        for room in rooms[:limit]:
            item = serialize_chat_room_preview_for_socket_sync(room, request.user, request_scope(request))
            if room.pk in allowed:
                data.append(item)
        has_more = len(rooms) > limit
        next_cursor = signing.dumps({'user': request.user.pk, 'at': rooms[limit - 1].updated_at.isoformat(), 'id': str(rooms[limit - 1].pk)}, salt=self.cursor_salt) if has_more else None
        response = Response({'message': 'Chat rooms fetched successfully', 'data': {
            'chat_rooms': data, 'has_more': has_more, 'limit': limit, 'next_cursor': next_cursor,
        }})
        response['Cache-Control'] = 'private, no-store'
        return response
