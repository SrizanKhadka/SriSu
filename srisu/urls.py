"""
URL configuration for srisu project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from authentication.api.views import *
from django.conf.urls.static import static
from django.http import HttpResponseNotFound
from django.urls import re_path
from django.views.static import serve
import posixpath
import os
from django.utils._os import safe_join


def deny_direct_moment_media(request, **kwargs):
    # Moment photos must go through the authenticated, expiry-aware API.
    return HttpResponseNotFound()


def serve_public_media(request, path, **kwargs):
    normalized = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    root = kwargs["document_root"]
    target = os.path.normcase(os.path.realpath(safe_join(root, normalized)))
    for prefix in ("couples/moments", "couples/profile_private", "couples/profile_photos", "couples/couple_chat_private"):
        private_root = os.path.normcase(os.path.realpath(os.path.join(root, *prefix.split("/"))))
        if (normalized.casefold() == prefix or normalized.casefold().startswith(prefix + "/")
                or os.path.commonpath([target, private_root]) == private_root):
            return HttpResponseNotFound()
    return serve(request, path, **kwargs)


urlpatterns = [
    re_path(r"^media/couples/(?:moments|profile_private|profile_photos|couple_chat_private)/", deny_direct_moment_media),
    path("admin/", admin.site.urls),
    path("api/auth/", include('authentication.urls')),
    path("api/chat/", include('chat.urls')),
    path("api/couple-chat/", include('couple_chat.urls')),
    path("api/social/", include('social.urls')),
] + static(settings.MEDIA_URL, view=serve_public_media, document_root=settings.MEDIA_ROOT)
