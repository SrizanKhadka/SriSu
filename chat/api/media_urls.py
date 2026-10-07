"""Stable first-party routes for authorization-aware legacy media delivery."""

import posixpath
from urllib.parse import quote


PRIVATE_PREFIXES = ("chats/media/", "messages/media/")


def guarded_media_path(file_field) -> str | None:
    if not file_field:
        return None
    name = posixpath.normpath(str(file_field.name).replace("\\", "/")).lstrip("/")
    if name.startswith("../") or not any(name.startswith(prefix) for prefix in PRIVATE_PREFIXES):
        return None
    return "/media/" + quote(name, safe="/")


def guarded_media_url(file_field, *, request=None, base_url: str = "") -> str | None:
    path = guarded_media_path(file_field)
    if path is None:
        return None
    if request is not None:
        return request.build_absolute_uri(path)
    return f"{base_url.rstrip('/')}{path}" if base_url else path
