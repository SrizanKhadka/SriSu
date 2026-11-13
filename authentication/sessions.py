"""Additive auth-1 sessions; legacy JWT policy remains explicitly configurable."""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.timezone import now
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from authentication.models import DeviceSession


def digest(value):
    return salted_hmac("srisu.refresh.jti", value, algorithm="sha256").hexdigest()


def credentials(session, request_id=None, replay=False):
    if not replay:
        session.previous_refresh_digest = session.refresh_digest
        session.rotation_id = request_id or uuid.uuid4()
        session.issued_at = now()
    refresh = RefreshToken.for_user(session.user)
    refresh["sid"] = str(session.pk)
    refresh["iat"] = int(session.issued_at.timestamp())
    refresh["jti"] = salted_hmac("srisu.session.rotation", f"{session.pk}:{session.rotation_id}", algorithm="sha256").hexdigest()
    # A session has an absolute 30-day maximum. Rotation doesn't extend it.
    refresh["exp"] = int(session.expires_at.timestamp())
    access = refresh.access_token
    access["iat"] = int(session.issued_at.timestamp())
    access["jti"] = digest("access:" + refresh["jti"])
    access.set_exp(from_time=session.issued_at, lifetime=timedelta(minutes=10))
    session.refresh_digest = digest(refresh["jti"])
    session.save(update_fields=["refresh_digest", "previous_refresh_digest", "rotation_id", "issued_at"])
    return {"access": str(access), "refresh": str(refresh)}


@transaction.atomic
def create_session(user):
    session = DeviceSession.objects.create(user=user, expires_at=now() + timedelta(days=30))
    return credentials(session)


def rotate_refresh(value, request_id=None):
    try:
        token = RefreshToken(value)
    except TokenError:
        raise AuthenticationFailed("Sign in again.") from None
    sid = token.get("sid")
    if not sid:
        # One-time migration for a valid legacy refresh token. Disable with the legacy cutoff.
        if not settings.AUTH_ACCEPT_LEGACY_TOKENS:
            raise AuthenticationFailed("Sign in again.")
        user = JWTAuthentication().get_user(token)
        # A stable session id derived from legacy jti prevents repeated upgrades creating sessions.
        stable_id = uuid.uuid5(uuid.NAMESPACE_URL, "srisu.legacy." + token["jti"])
        with transaction.atomic():
            session, created = DeviceSession.objects.get_or_create(id=stable_id, defaults={
                "user": user, "expires_at": now() + timedelta(days=30)})
            session = DeviceSession.objects.select_for_update().get(pk=session.pk)
            if not created:
                if request_id and session.rotation_id == request_id and not session.revoked_at and session.issued_at + timedelta(seconds=60) > now():
                    return credentials(session, replay=True)
                raise AuthenticationFailed("Sign in again.")
            return credentials(session, request_id)
    error = False
    with transaction.atomic():
        session = DeviceSession.objects.select_for_update().filter(pk=sid, user_id=token["user_id"]).first()
        if session is None or session.revoked_at or session.expires_at <= now() or not session.user.is_active:
            error = True
        elif (request_id and session.rotation_id == request_id
              and session.issued_at + timedelta(seconds=60) > now()
              and constant_time_compare(session.previous_refresh_digest, digest(token["jti"]))):
            result = credentials(session, replay=True)
        elif not constant_time_compare(session.refresh_digest, digest(token["jti"])):
            session.revoked_at = now()
            session.save(update_fields=["revoked_at"])
            error = True
        else:
            result = credentials(session, request_id)
    if error:
        raise AuthenticationFailed("Sign in again.")
    return result


class SessionJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        sid = validated_token.get("sid")
        if sid:
            if not DeviceSession.objects.filter(pk=sid, user=user, revoked_at__isnull=True, expires_at__gt=now()).exists():
                raise AuthenticationFailed("Session expired.")
        elif not settings.AUTH_ACCEPT_LEGACY_TOKENS:
            raise AuthenticationFailed("Sign in again.")
        return user

    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, token = result
            if not user.is_phone_verified or not user.is_profile_complete:
                if request.path.rstrip("/") not in ("/api/auth/setup-profile", "/api/auth/logout", "/api/auth/interests"):
                    raise PermissionDenied("Complete your profile first.")
        return result
