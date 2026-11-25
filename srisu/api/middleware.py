"""Correlation and bounded metadata only: never URL queries, bodies or identities."""
import logging
from time import monotonic

from srisu.api.errors import CONTRACT, correlation_id

logger = logging.getLogger("srisu.http")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = monotonic()
        request.request_id = correlation_id(request.headers.get("X-Request-ID"))
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        response["X-SriSu-Contract"] = CONTRACT
        match = getattr(request, "resolver_match", None)
        route = getattr(match, "route", None) or "unresolved"
        logger.info("request_complete", extra={
            "request_id": request.request_id, "route": route,
            "status": response.status_code,
            "duration_ms": round((monotonic() - started) * 1000, 2),
        })
        return response
