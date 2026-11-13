"""Authenticated Matrix application-service HTTP surface (not a user API)."""
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from .matrix_push import ingest

logger = logging.getLogger("srisu.matrix_push")
MAX_BODY = 8 * 1024 * 1024
MAX_EVENTS = 1000


def error(code, status):
    return JsonResponse({"errcode": code, "error": "Matrix notification request rejected."}, status=status)


@csrf_exempt
@sensitive_variables()
def application_service(request, transaction_id=None, operation=None, unused=None):
    secret = settings.MATRIX_PUSH_HS_TOKEN
    # Modern Synapse uses only the Authorization header; never accept credentials
    # in a URL where proxies and access logs could retain them.
    if (len(secret) < 32 or request.GET or not constant_time_compare(
            request.headers.get("Authorization", ""), "Bearer " + secret)):
        return error("M_FORBIDDEN", 403)
    if operation == "ping" and request.method == "POST":
        return JsonResponse({})
    if operation is not None or request.method != "PUT" or not transaction_id or len(transaction_id) > 128:
        return error("M_UNRECOGNIZED", 404)
    if request.content_type != "application/json":
        return error("M_BAD_JSON", 400)
    try:
        raw = request.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            return error("M_TOO_LARGE", 413)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError()
        for key in ("events", "ephemeral"):
            items = payload.get(key, [] if key == "ephemeral" else None)
            if not isinstance(items, list) or len(items) > MAX_EVENTS:
                raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        return error("M_BAD_JSON", 400)
    try:
        ingest(transaction_id, payload)
    except Exception as failure:
        # Fail the entire transaction so Synapse retries. Never echo/store/log
        # bodies, ciphertext, tokens, event IDs, user IDs or exception strings.
        logger.error("matrix_push_transaction_failed", extra={"exception_type": type(failure).__name__})
        return error("M_UNKNOWN", 503)
    return JsonResponse({})
