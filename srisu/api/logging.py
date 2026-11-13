"""Small allowlisted JSON diagnostic format; exception text and payloads are excluded."""
import json
import logging

EVENTS = {
    'request_complete', 'unhandled_api_error', 'cache_hit', 'cache_miss',
    'cache_read_unavailable', 'cache_write_unavailable', 'cache_invalidation_unavailable',
    'socket_publication_unavailable', 'socket_access_check_unavailable',
}
FIELDS = ('request_id', 'route', 'status', 'duration_ms', 'namespace', 'exception_type')


class CoreJsonFormatter(logging.Formatter):
    def format(self, record):
        data = {'event': record.msg if isinstance(record.msg, str) and record.msg in EVENTS else 'diagnostic',
                'level': record.levelname}
        for name in FIELDS:
            value = getattr(record, name, None)
            if isinstance(value, (int, float, str)):
                data[name] = value
        return json.dumps(data)
