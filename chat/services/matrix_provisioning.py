"""Retryable private-Matrix provisioning outside database transactions.

The concrete Matrix adapter owns network and authentication details.  This
module owns deterministic identities, current-couple authorization and durable
provision/revocation state.  It never stores Matrix access/login tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import logging
import re
import secrets
from urllib.parse import quote, urlparse

import jwt
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from authentication.models import UserModel
from chat.matrix_readiness import ROOM_POLICY_VERSION
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping
from chat.selectors.access import authorized_rooms


logger = logging.getLogger("srisu.matrix")
ROOM_DRIFT_ERROR_CODES = {
    "matrix_room_access_mismatch",
    "matrix_room_mismatch",
    "matrix_room_policy_mismatch",
}


class MatrixServiceError(Exception):
    def __init__(self, code: str = "matrix_unavailable"):
        self.code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,40}", code)
            else "matrix_unavailable"
        )
        super().__init__(self.code)


class MatrixUnavailable(MatrixServiceError):
    pass


@dataclass(frozen=True)
class MatrixProvisionedRoom:
    room_id: str
    room_alias: str


@dataclass(frozen=True)
class MatrixLoginSession:
    login_type: str
    login_token: str
    expires_at: datetime


@dataclass(frozen=True)
class MatrixRoomVerificationSnapshot:
    """Exact local generation whose remote policy was inspected."""

    chat_room_id: object
    matrix_room_id: str
    matrix_room_alias: str
    membership_epoch: int
    member_matrix_localparts: tuple[str, ...]


@dataclass(frozen=True)
class MatrixProvisioningClaim:
    chat_room_id: object
    attempts: int
    locked_at: datetime
    membership_epoch: int
    room_alias_localpart: str
    member_matrix_localparts: tuple[str, ...]


class DisabledMatrixService:
    """Fail-closed default; deployments must supply a reviewed adapter."""

    def _unavailable(self):
        raise MatrixUnavailable("matrix_not_configured")

    def provision_user(self, matrix_user_id: str) -> str:
        self._unavailable()

    def provision_room(
        self,
        *,
        room_alias: str,
        initiator_user_id: str,
        partner_user_id: str,
    ) -> MatrixProvisionedRoom:
        self._unavailable()

    def issue_login_token(
        self,
        *,
        matrix_user_id: str,
        django_session_id: str,
        ttl_seconds: int,
    ) -> MatrixLoginSession:
        self._unavailable()

    def revoke_room(
        self,
        *,
        room_id: str | None,
        room_alias: str,
        member_user_ids: list[str],
    ) -> str | None:
        self._unavailable()

    def verify_room(
        self,
        *,
        room_id: str,
        room_alias: str,
        member_user_ids: list[str],
    ) -> None:
        self._unavailable()

    def verify_policy(self) -> None:
        self._unavailable()


class SynapseMatrixService:
    """Synapse client/API adapter using short-lived, server-signed JWT login.

    JWT login creates a real Matrix device. The mobile client supplies its
    stable device id to ``/_matrix/client/v3/login`` and then uses only normal
    Matrix APIs. Provisioning uses short-lived throwaway devices which are
    logged out before returning.
    """

    login_type = "org.matrix.login.jwt"

    def __init__(self):
        self.internal_url = settings.MATRIX_INTERNAL_HOMESERVER_URL.rstrip("/")
        self.server_name = settings.MATRIX_SERVER_NAME
        self.jwt_secret = settings.MATRIX_JWT_SECRET
        self.jwt_issuer = settings.MATRIX_JWT_ISSUER
        self.jwt_audience = settings.MATRIX_JWT_AUDIENCE
        self.timeout = settings.MATRIX_HTTP_TIMEOUT_SECONDS
        provisioning_localpart = settings.MATRIX_PROVISIONING_LOCALPART
        if not re.fullmatch(r"[a-z0-9._=-]{1,64}", provisioning_localpart):
            raise MatrixUnavailable("matrix_provisioning_identity_invalid")
        self.provisioning_user_id = f"@{provisioning_localpart}:{self.server_name}"
        self.http = requests.Session()
        # Matrix credentials must never be forwarded through ambient process
        # proxies; deployment networking is configured explicitly.
        self.http.trust_env = False
        if not all([
            self.internal_url,
            self.server_name,
            self.jwt_secret,
            self.jwt_issuer,
            self.jwt_audience,
        ]):
            raise MatrixUnavailable("matrix_not_configured")
        # The checked-in Synapse development configuration and this issuer
        # deliberately support one reviewed algorithm only.
        if settings.MATRIX_JWT_ALGORITHM != "HS256":
            raise MatrixUnavailable("matrix_algorithm_unsupported")
        if len(self.jwt_secret.encode("utf-8")) < 32:
            raise MatrixUnavailable("matrix_secret_invalid")
        if not 0.5 <= self.timeout <= 30:
            raise MatrixUnavailable("matrix_timeout_invalid")
        if not 30 <= settings.MATRIX_SESSION_TTL_SECONDS <= 300:
            raise MatrixUnavailable("matrix_session_ttl_invalid")
        if not 300 <= settings.MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS <= 900:
            raise MatrixUnavailable("matrix_access_ttl_invalid")
        if settings.MATRIX_ROOM_VERSION != "11":
            raise MatrixUnavailable("matrix_room_version_unsupported")
        if not _valid_matrix_origin(self.internal_url, require_https=False):
            raise MatrixUnavailable("matrix_internal_url_invalid")
        if not _valid_matrix_origin(
            settings.MATRIX_HOMESERVER_URL,
            require_https=not settings.DEBUG,
        ):
            raise MatrixUnavailable("matrix_public_url_invalid")

    def _localpart(self, matrix_user_id: str) -> str:
        suffix = f":{self.server_name}"
        if not matrix_user_id.startswith("@") or not matrix_user_id.endswith(suffix):
            raise MatrixServiceError("matrix_identity_mismatch")
        localpart = matrix_user_id[1:-len(suffix)]
        if not localpart or ":" in localpart:
            raise MatrixServiceError("matrix_identity_mismatch")
        return localpart

    def _jwt(self, matrix_user_id: str, *, ttl_seconds: int, session_id: str) -> tuple[str, datetime]:
        now = timezone.now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        claims = {
            "sub": self._localpart(matrix_user_id),
            "iss": self.jwt_issuer,
            "aud": self.jwt_audience,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_urlsafe(24),
            # Bind the credential to the Django session without disclosing the
            # raw session identifier in the readable JWT payload.
            "srisu_sid_hash": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        }
        token = jwt.encode(
            claims,
            self.jwt_secret,
            algorithm=settings.MATRIX_JWT_ALGORITHM,
        )
        return token, expires_at

    def _request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        access_token: str | None = None,
        allowed_statuses=(200,),
        drift_on_missing: bool = False,
    ):
        headers = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = self.http.request(
                method,
                f"{self.internal_url}{path}",
                json=body,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise MatrixUnavailable("matrix_remote_unavailable") from None
        if response.status_code not in allowed_statuses:
            if response.status_code == 429 or response.status_code >= 500:
                raise MatrixUnavailable("matrix_remote_unavailable")
            if response.status_code == 401:
                raise MatrixUnavailable("matrix_remote_auth_failed")
            if response.status_code in {403, 404}:
                if drift_on_missing:
                    raise MatrixServiceError("matrix_room_access_mismatch")
                # Login/configuration rejection is not evidence that the
                # already-provisioned room drifted. Keep it retryable so a bad
                # JWT/config rollout cannot rotate valid room history.
                raise MatrixUnavailable("matrix_remote_auth_failed")
            raise MatrixServiceError("matrix_remote_rejected")
        if not response.content:
            return response.status_code, {}
        try:
            return response.status_code, response.json()
        except ValueError:
            raise MatrixServiceError("matrix_response_invalid") from None

    def _login(self, matrix_user_id: str):
        device_id = f"SRISUPROV{secrets.token_hex(8).upper()}"
        token, _ = self._jwt(
            matrix_user_id,
            ttl_seconds=60,
            session_id=device_id,
        )
        _, data = self._request(
            "POST",
            "/_matrix/client/v3/login",
            body={
                "type": self.login_type,
                "token": token,
                "device_id": device_id,
                "initial_device_display_name": "SriSu provisioning",
                "refresh_token": False,
            },
        )
        if (
            data.get("user_id") != matrix_user_id
            or data.get("device_id") != device_id
            or not data.get("access_token")
        ):
            raise MatrixServiceError("matrix_login_mismatch")
        return data["access_token"]

    def _logout(self, access_token: str) -> None:
        self._request(
            "POST",
            "/_matrix/client/v3/logout",
            body={},
            access_token=access_token,
        )

    def _with_temporary_sessions(self, user_ids: list[str], operation):
        sessions = []
        operation_error = None
        result = None
        try:
            for user_id in user_ids:
                sessions.append((user_id, self._login(user_id)))
            result = operation(dict(sessions))
        except Exception as exc:  # normalized by the caller's service boundary
            operation_error = exc
        cleanup_failed = False
        for _, access_token in reversed(sessions):
            try:
                self._logout(access_token)
            except MatrixServiceError:
                cleanup_failed = True
        if operation_error is not None:
            raise operation_error
        if cleanup_failed:
            raise MatrixServiceError("matrix_provisioning_logout_failed")
        return result

    def provision_user(self, matrix_user_id: str) -> str:
        # JWT login auto-registers local users. Room provisioning below performs
        # that login for both members, so this method only validates the mapping.
        self._localpart(matrix_user_id)
        return matrix_user_id

    def verify_policy(self) -> None:
        _, payload = self._request(
            "GET",
            "/_synapse/client/srisu/policy/v1",
        )
        if payload != {
            "policy_version": ROOM_POLICY_VERSION,
            "server_name": self.server_name,
            "provisioning_user_id": self.provisioning_user_id,
        }:
            raise MatrixUnavailable("matrix_policy_probe_mismatch")

    def _power_levels(self, member_user_ids: list[str]):
        locked_state_events = {
            event_type: 100
            for event_type in (
                "m.room.canonical_alias",
                "m.room.encryption",
                "m.room.guest_access",
                "m.room.history_visibility",
                "m.room.join_rules",
                "m.room.name",
                "m.room.power_levels",
                "m.room.server_acl",
                "m.room.topic",
            )
        }
        return {
            "users": {
                self.provisioning_user_id: 100,
                **{user_id: 0 for user_id in member_user_ids},
            },
            "users_default": 0,
            "events": locked_state_events,
            "events_default": 0,
            "state_default": 100,
            "ban": 100,
            "kick": 100,
            "redact": 100,
            "invite": 100,
        }

    def _alias_localpart(self, room_alias: str) -> str:
        expected_suffix = f":{self.server_name}"
        if not room_alias.startswith("#") or not room_alias.endswith(expected_suffix):
            raise MatrixServiceError("matrix_room_mismatch")
        localpart = room_alias[1:-len(expected_suffix)]
        if not re.fullmatch(r"srisu_chat_[0-9a-f]{32}_e[1-9][0-9]*", localpart):
            raise MatrixServiceError("matrix_room_mismatch")
        return localpart

    def _verify_room(
        self,
        *,
        room_id: str,
        member_user_ids: list[str],
        access_token: str,
    ) -> None:
        room_path = quote(room_id, safe="")
        _, state_events = self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{room_path}/state",
            access_token=access_token,
            drift_on_missing=True,
        )
        if not isinstance(state_events, list):
            raise MatrixServiceError("matrix_response_invalid")
        state = {
            (event.get("type"), event.get("state_key")): event
            for event in state_events
            if isinstance(event, dict)
        }
        creation_event = state.get(("m.room.create", ""), {})
        creation = creation_event.get("content", {})
        encryption = state.get(("m.room.encryption", ""), {}).get("content", {})
        join_rules = state.get(("m.room.join_rules", ""), {}).get("content", {})
        guest_access = state.get(("m.room.guest_access", ""), {}).get("content", {})
        history = state.get(("m.room.history_visibility", ""), {}).get("content", {})
        power = state.get(("m.room.power_levels", ""), {}).get("content", {})
        _, directory = self._request(
            "GET",
            f"/_matrix/client/v3/directory/list/room/{room_path}",
            access_token=access_token,
            drift_on_missing=True,
        )
        _, members = self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{room_path}/members",
            access_token=access_token,
            drift_on_missing=True,
        )
        active_members = {
            event.get("state_key"): event.get("content", {}).get("membership")
            for event in members.get("chunk", [])
            if event.get("content", {}).get("membership") != "leave"
        }
        expected_power = self._power_levels(member_user_ids)
        locked_events = expected_power["events"]
        power_users = power.get("users", {})
        power_events = power.get("events", {})
        if (
            # Room v11 removed ``creator`` from create-event content. The
            # event sender is the authoritative creator identity.
            creation_event.get("sender") != self.provisioning_user_id
            or creation.get("m.federate") is not False
            or encryption.get("algorithm") != "m.megolm.v1.aes-sha2"
            or join_rules.get("join_rule") != "invite"
            or guest_access.get("guest_access") != "forbidden"
            or history.get("history_visibility") != "invited"
            or directory.get("visibility") != "private"
            or set(active_members) != set(member_user_ids)
            or any(membership != "join" for membership in active_members.values())
            or power_users != expected_power["users"]
            or power.get("users_default") != 0
            or power.get("events_default") != 0
            or power.get("state_default") != 100
            or any(power.get(name) != 100 for name in ("ban", "kick", "redact", "invite"))
            or not isinstance(power_events, dict)
            or any(power_events.get(name) != 100 for name in locked_events)
            # Explicit event entries override state_default. Unknown entries
            # are acceptable only when equally locked; a reused alias must not
            # retain an unreviewed PL0 state-event escape hatch.
            or any(level != 100 for level in power_events.values())
        ):
            raise MatrixServiceError("matrix_room_policy_mismatch")

    def provision_room(
        self,
        *,
        room_alias: str,
        initiator_user_id: str,
        partner_user_id: str,
    ) -> MatrixProvisionedRoom:
        alias_localpart = self._alias_localpart(room_alias)

        member_user_ids = [initiator_user_id, partner_user_id]

        def operation(sessions):
            alias_path = quote(room_alias, safe="")
            status_code, alias_data = self._request(
                "GET",
                f"/_matrix/client/v3/directory/room/{alias_path}",
                access_token=sessions[self.provisioning_user_id],
                allowed_statuses=(200, 404),
            )
            if status_code == 200:
                room_id = alias_data.get("room_id")
            else:
                _, created = self._request(
                    "POST",
                    "/_matrix/client/v3/createRoom",
                    access_token=sessions[self.provisioning_user_id],
                    body={
                        "visibility": "private",
                        "preset": "private_chat",
                        "room_version": settings.MATRIX_ROOM_VERSION,
                        "room_alias_name": alias_localpart,
                        "invite": member_user_ids,
                        "is_direct": True,
                        "creation_content": {"m.federate": False},
                        "power_level_content_override": self._power_levels(member_user_ids),
                        "initial_state": [
                            {
                                "type": "m.room.encryption",
                                "state_key": "",
                                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
                            },
                            {
                                "type": "m.room.join_rules",
                                "state_key": "",
                                "content": {"join_rule": "invite"},
                            },
                            {
                                "type": "m.room.guest_access",
                                "state_key": "",
                                "content": {"guest_access": "forbidden"},
                            },
                            {
                                "type": "m.room.history_visibility",
                                "state_key": "",
                                "content": {"history_visibility": "invited"},
                            },
                        ],
                    },
                )
                room_id = created.get("room_id")
                if room_id:
                    room_path = quote(room_id, safe="")
                    self._request(
                        "PUT",
                        f"/_matrix/client/v3/directory/list/room/{room_path}",
                        access_token=sessions[self.provisioning_user_id],
                        body={"visibility": "private"},
                    )
            if not room_id:
                raise MatrixServiceError("matrix_room_mismatch")
            room_path = quote(room_id, safe="")
            for user_id in member_user_ids:
                _, joined = self._request(
                    "POST",
                    f"/_matrix/client/v3/join/{room_path}",
                    access_token=sessions[user_id],
                    body={},
                )
                if joined.get("room_id") != room_id:
                    raise MatrixServiceError("matrix_room_mismatch")
            self._request(
                "POST",
                f"/_matrix/client/v3/rooms/{room_path}/leave",
                access_token=sessions[self.provisioning_user_id],
                body={},
                allowed_statuses=(200, 403, 404),
            )
            self._verify_room(
                room_id=room_id,
                member_user_ids=member_user_ids,
                access_token=sessions[initiator_user_id],
            )
            return MatrixProvisionedRoom(room_id=room_id, room_alias=room_alias)

        return self._with_temporary_sessions(
            [self.provisioning_user_id, initiator_user_id, partner_user_id],
            operation,
        )

    def verify_room(
        self,
        *,
        room_id: str,
        room_alias: str,
        member_user_ids: list[str],
    ) -> None:
        self._alias_localpart(room_alias)
        if not room_id or len(set(member_user_ids)) != 2:
            raise MatrixServiceError("matrix_room_mismatch")

        def operation(sessions):
            alias_path = quote(room_alias, safe="")
            status_code, alias_data = self._request(
                "GET",
                f"/_matrix/client/v3/directory/room/{alias_path}",
                access_token=sessions[self.provisioning_user_id],
                allowed_statuses=(200, 404),
            )
            if status_code != 200 or alias_data.get("room_id") != room_id:
                raise MatrixServiceError("matrix_room_policy_mismatch")
            self._verify_room(
                room_id=room_id,
                member_user_ids=member_user_ids,
                access_token=sessions[member_user_ids[0]],
            )

        self._with_temporary_sessions(
            [self.provisioning_user_id, member_user_ids[0]],
            operation,
        )

    def issue_login_token(
        self,
        *,
        matrix_user_id: str,
        django_session_id: str,
        ttl_seconds: int,
    ) -> MatrixLoginSession:
        token, expires_at = self._jwt(
            matrix_user_id,
            ttl_seconds=ttl_seconds,
            session_id=django_session_id,
        )
        return MatrixLoginSession(
            login_type=self.login_type,
            login_token=token,
            expires_at=expires_at,
        )

    def revoke_room(
        self,
        *,
        room_id: str | None,
        room_alias: str,
        member_user_ids: list[str],
    ) -> str | None:
        if len(set(member_user_ids)) != 2:
            raise MatrixServiceError("matrix_mapping_missing")
        if room_id is None:
            alias_path = quote(room_alias, safe="")

            def resolve(sessions):
                status_code, data = self._request(
                    "GET",
                    f"/_matrix/client/v3/directory/room/{alias_path}",
                    access_token=sessions[self.provisioning_user_id],
                    allowed_statuses=(200, 404),
                )
                if status_code == 404:
                    return None
                resolved = data.get("room_id")
                if not resolved:
                    raise MatrixServiceError("matrix_room_mismatch")
                return resolved

            room_id = self._with_temporary_sessions(
                [self.provisioning_user_id],
                resolve,
            )
            if room_id is None:
                return None

        def operation(sessions):
            room_path = quote(room_id, safe="")
            for user_id, access_token in sessions.items():
                self._request(
                    "POST",
                    f"/_matrix/client/v3/rooms/{room_path}/leave",
                    access_token=access_token,
                    body={},
                    # A former/non-member is already revoked from this room.
                    allowed_statuses=(200, 403, 404),
                )

        self._with_temporary_sessions(member_user_ids, operation)
        return room_id


def _valid_matrix_origin(value: str, *, require_https: bool) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ({"https"} if require_https else {"http", "https"}):
            return False
        if not parsed.hostname or parsed.username or parsed.password:
            return False
        parsed.port
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        return True
    except ValueError:
        return False


def get_matrix_service():
    if not settings.MATRIX_ENABLED:
        return DisabledMatrixService()
    if not all([
        settings.MATRIX_HOMESERVER_URL,
        settings.MATRIX_INTERNAL_HOMESERVER_URL,
        settings.MATRIX_SERVER_NAME,
        settings.MATRIX_JWT_SECRET,
    ]):
        return DisabledMatrixService()
    if settings.MATRIX_JWT_SECRET.startswith("replace-"):
        return DisabledMatrixService()
    if not _valid_matrix_origin(
        settings.MATRIX_HOMESERVER_URL,
        require_https=not settings.DEBUG,
    ) or not _valid_matrix_origin(
        settings.MATRIX_INTERNAL_HOMESERVER_URL,
        require_https=False,
    ):
        return DisabledMatrixService()
    try:
        service_class = import_string(settings.MATRIX_SERVICE_CLASS)
        return service_class()
    except MatrixServiceError:
        raise
    except Exception:
        raise MatrixUnavailable("matrix_adapter_invalid") from None


def matrix_localpart(user_id: int) -> str:
    return f"srisu_user_{user_id}"


def matrix_room_alias_localpart(room_id, epoch: int = 1) -> str:
    return f"srisu_chat_{room_id.hex}_e{epoch}"


def _matrix_user_id(localpart: str) -> str:
    if not settings.MATRIX_SERVER_NAME:
        raise MatrixUnavailable("matrix_not_configured")
    return f"@{localpart}:{settings.MATRIX_SERVER_NAME}"


def _matrix_room_alias(localpart: str) -> str:
    if not settings.MATRIX_SERVER_NAME:
        raise MatrixUnavailable("matrix_not_configured")
    return f"#{localpart}:{settings.MATRIX_SERVER_NAME}"


def ensure_matrix_mapping_records(room: ChatRoom) -> MatrixRoomMapping:
    """Database-only preparation, safe to call inside relationship commit."""
    user_ids = sorted(
        user_id
        for user_id in [room.user_one_id, room.user_two_id]
        if user_id is not None
    )
    member_localparts = [matrix_localpart(user_id) for user_id in user_ids]
    for user_id in user_ids:
        if user_id is not None:
            MatrixUserMapping.objects.get_or_create(
                user_id=user_id,
                defaults={"matrix_localpart": matrix_localpart(user_id)},
            )
    mapping, created = MatrixRoomMapping.objects.get_or_create(
        chat_room=room,
        defaults={
            "membership_epoch": 1,
            "room_alias_localpart": matrix_room_alias_localpart(room.pk),
            "member_matrix_localparts": member_localparts,
        },
    )
    if not created and mapping.member_matrix_localparts != member_localparts:
        # Participant replacement is not an ordinary profile/chat update. If
        # provisioning may already have reached Matrix, preserve the original
        # member snapshot for revocation and fail closed instead of silently
        # retargeting a stable alias to a different human.
        if (
            mapping.state == MatrixRoomMapping.State.PENDING
            and mapping.attempts == 0
            and mapping.matrix_room_id is None
        ):
            mapping.member_matrix_localparts = member_localparts
            mapping.save(update_fields=["member_matrix_localparts", "updated_at"])
        else:
            now = timezone.now()
            provisioning_inflight = (
                mapping.state == MatrixRoomMapping.State.PROVISIONING
                and mapping.locked_at is not None
                and mapping.locked_at
                > now - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
            )
            mapping.state = MatrixRoomMapping.State.REVOKE_PENDING
            mapping.replacement_pending = True
            mapping.available_at = (
                mapping.locked_at
                + timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
                if provisioning_inflight
                else now
            )
            if not provisioning_inflight:
                mapping.locked_at = None
            mapping.last_error_code = "matrix_membership_changed"
            mapping.save(update_fields=[
                "state",
                "replacement_pending",
                "available_at",
                "locked_at",
                "last_error_code",
                "updated_at",
            ])
    return mapping


def _retry_at(attempts: int):
    exponent = max(0, min(attempts - 1, 8))
    seconds = min(
        settings.MATRIX_RETRY_MAX_SECONDS,
        settings.MATRIX_RETRY_BASE_SECONDS * (2**exponent),
    )
    return timezone.now() + timedelta(seconds=seconds)


def _record_provision_failure(
    mapping_id: int,
    code: str,
    claim: MatrixProvisioningClaim,
) -> None:
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if (
            mapping.attempts != claim.attempts
            or mapping.locked_at != claim.locked_at
            or mapping.membership_epoch != claim.membership_epoch
            or mapping.room_alias_localpart != claim.room_alias_localpart
            or mapping.chat_room_id != claim.chat_room_id
            or tuple(mapping.member_matrix_localparts)
            != claim.member_matrix_localparts
        ):
            return
        if mapping.state == MatrixRoomMapping.State.REVOKE_PENDING:
            mapping.locked_at = None
            mapping.last_error_code = code[:40]
            mapping.available_at = timezone.now()
            mapping.save(update_fields=[
                "locked_at",
                "last_error_code",
                "available_at",
                "updated_at",
            ])
            return
        if mapping.state != MatrixRoomMapping.State.PROVISIONING:
            return
        mapping.state = MatrixRoomMapping.State.FAILED
        mapping.locked_at = None
        mapping.last_error_code = code[:40]
        mapping.available_at = _retry_at(mapping.attempts)
        mapping.save(update_fields=[
            "state",
            "locked_at",
            "last_error_code",
            "available_at",
            "updated_at",
        ])


def _queue_remote_revocation(
    mapping_id: int,
    *,
    room_id: str,
    room_alias: str,
    member_localparts: tuple[str, ...],
    membership_epoch: int,
) -> None:
    """Durably retain cleanup for a room returned by a stale provisioner."""
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        queue = list(mapping.pending_remote_revocations)
        if any(item.get("room_id") == room_id for item in queue):
            return
        queue.append({
            "room_id": room_id,
            "room_alias": room_alias,
            "member_matrix_localparts": list(member_localparts),
            "membership_epoch": membership_epoch,
            "attempts": 0,
            "available_at": timezone.now().isoformat(),
            "last_error_code": "",
        })
        mapping.pending_remote_revocations = queue
        mapping.save(update_fields=["pending_remote_revocations", "updated_at"])


def process_pending_remote_revocation(mapping_id: int, *, service=None) -> bool:
    """Process one exact stale-room cleanup without touching the live generation."""
    now = timezone.now()
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        queue = list(mapping.pending_remote_revocations)
        claim_index = None
        for index, item in enumerate(queue):
            try:
                available_at = datetime.fromisoformat(item["available_at"])
            except (KeyError, TypeError, ValueError):
                available_at = now
            if timezone.is_naive(available_at):
                available_at = timezone.make_aware(available_at)
            if available_at <= now:
                claim_index = index
                break
        if claim_index is None:
            return not queue
        item = dict(queue[claim_index])
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["available_at"] = _retry_at(item["attempts"]).isoformat()
        item["last_error_code"] = ""
        queue[claim_index] = item
        mapping.pending_remote_revocations = queue
        mapping.save(update_fields=["pending_remote_revocations", "updated_at"])

    try:
        matrix = service or get_matrix_service()
        matrix.revoke_room(
            room_id=item["room_id"],
            room_alias=item["room_alias"],
            member_user_ids=[
                _matrix_user_id(localpart)
                for localpart in item["member_matrix_localparts"]
            ],
        )
    except MatrixServiceError as exc:
        error_code = exc.code
    except Exception:
        error_code = "matrix_remote_unavailable"
    else:
        error_code = ""

    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        queue = list(mapping.pending_remote_revocations)
        matching = [
            (index, queued)
            for index, queued in enumerate(queue)
            if queued.get("room_id") == item["room_id"]
            and int(queued.get("attempts", 0)) == item["attempts"]
        ]
        if not matching:
            return not queue
        index, queued = matching[0]
        if error_code:
            queued = dict(queued)
            queued["last_error_code"] = error_code[:40]
            queue[index] = queued
        else:
            queue.pop(index)
        mapping.pending_remote_revocations = queue
        mapping.save(update_fields=["pending_remote_revocations", "updated_at"])
        return not queue


def _claim_provisioning(mapping_id: int):
    with transaction.atomic():
        # Lock only the durable mapping row. ``chat_room`` is nullable by
        # design, and PostgreSQL rejects FOR UPDATE across the nullable side of
        # the outer join that select_related() would introduce.
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if mapping.pending_remote_revocations:
            return None
        if mapping.state == MatrixRoomMapping.State.ACTIVE:
            return "active"
        if mapping.state in {
            MatrixRoomMapping.State.REVOKE_PENDING,
            MatrixRoomMapping.State.REVOKING,
        }:
            return None
        if mapping.state == MatrixRoomMapping.State.REVOKED:
            room = (
                ChatRoom.objects.filter(pk=mapping.chat_room_id).first()
                if mapping.chat_room_id is not None
                else None
            )
            if (
                room is None
                or not authorized_rooms(room.user_one).filter(pk=room.pk).exists()
            ):
                return None
            # Never reopen a revoked Matrix room when a relationship becomes
            # eligible again. Advance exactly once to a fresh alias; failed
            # retries remain on this new generation.
            now = timezone.now()
            mapping.membership_epoch += 1
            mapping.room_alias_localpart = matrix_room_alias_localpart(
                room.pk,
                mapping.membership_epoch,
            )
            mapping.matrix_room_id = None
            mapping.matrix_room_alias = None
            mapping.member_matrix_localparts = [
                matrix_localpart(user_id)
                for user_id in sorted([room.user_one_id, room.user_two_id])
            ]
            mapping.state = MatrixRoomMapping.State.PENDING
            mapping.replacement_pending = False
            mapping.attempts = 0
            mapping.available_at = now
            mapping.locked_at = None
            mapping.last_error_code = ""
            mapping.provisioned_at = None
            mapping.remote_verified_at = None
            mapping.save(update_fields=[
                "membership_epoch",
                "room_alias_localpart",
                "matrix_room_id",
                "matrix_room_alias",
                "member_matrix_localparts",
                "state",
                "replacement_pending",
                "attempts",
                "available_at",
                "locked_at",
                "last_error_code",
                "provisioned_at",
                "remote_verified_at",
                "updated_at",
            ])
        if mapping.chat_room_id is None:
            mapping.state = MatrixRoomMapping.State.REVOKE_PENDING
            mapping.available_at = timezone.now()
            mapping.locked_at = None
            mapping.last_error_code = ""
            mapping.save(update_fields=[
                "state",
                "available_at",
                "locked_at",
                "last_error_code",
                "updated_at",
            ])
            return None
        stale_before = timezone.now() - timedelta(
            seconds=settings.MATRIX_PROVISION_LOCK_SECONDS
        )
        if (
            mapping.state == MatrixRoomMapping.State.PROVISIONING
            and mapping.locked_at
            and mapping.locked_at > stale_before
        ):
            return None
        if mapping.available_at > timezone.now():
            return None
        mapping.state = MatrixRoomMapping.State.PROVISIONING
        mapping.attempts += 1
        mapping.locked_at = timezone.now()
        mapping.last_error_code = ""
        mapping.save(update_fields=[
            "state",
            "attempts",
            "locked_at",
            "last_error_code",
            "updated_at",
        ])
        return MatrixProvisioningClaim(
            chat_room_id=mapping.chat_room_id,
            attempts=mapping.attempts,
            locked_at=mapping.locked_at,
            membership_epoch=mapping.membership_epoch,
            room_alias_localpart=mapping.room_alias_localpart,
            member_matrix_localparts=tuple(mapping.member_matrix_localparts),
        )


def provision_matrix_room(mapping_id: int, *, service=None) -> bool:
    """Perform remote idempotent provisioning with no open DB transaction."""
    claim = _claim_provisioning(mapping_id)
    if claim == "active":
        return True
    if claim is None:
        return False
    mapping = (
        MatrixRoomMapping.objects.select_related("chat_room")
        .get(pk=mapping_id)
    )
    if not isinstance(claim, MatrixProvisioningClaim):
        return False
    room = mapping.chat_room
    if room is None or room.pk != claim.chat_room_id:
        return False
    if not authorized_rooms(room.user_one).filter(pk=room.pk).exists():
        mark_matrix_room_revoke_pending(room)
        return False
    current_localparts = tuple(
        matrix_localpart(user_id)
        for user_id in sorted([room.user_one_id, room.user_two_id])
    )
    if current_localparts != claim.member_matrix_localparts:
        ensure_matrix_mapping_records(room)
        return False
    user_mappings = {
        item.matrix_localpart: item
        for item in MatrixUserMapping.objects.filter(
            matrix_localpart__in=claim.member_matrix_localparts
        )
    }
    if set(user_mappings) != set(claim.member_matrix_localparts):
        _record_provision_failure(mapping.pk, "matrix_mapping_missing", claim)
        return False
    claim_user_ids = [
        user_mappings[localpart].user_id
        for localpart in claim.member_matrix_localparts
    ]
    try:
        matrix = service or get_matrix_service()
        provisioned_users = {}
        for localpart in claim.member_matrix_localparts:
            user_id = user_mappings[localpart].user_id
            expected = _matrix_user_id(localpart)
            actual = matrix.provision_user(expected)
            if actual != expected:
                raise MatrixServiceError("matrix_identity_mismatch")
            provisioned_users[user_id] = actual
        alias = _matrix_room_alias(claim.room_alias_localpart)
        room_result = matrix.provision_room(
            room_alias=alias,
            initiator_user_id=provisioned_users[claim_user_ids[0]],
            partner_user_id=provisioned_users[claim_user_ids[1]],
        )
        if room_result.room_alias != alias or not room_result.room_id:
            raise MatrixServiceError("matrix_room_mismatch")
    except MatrixServiceError as exc:
        _record_provision_failure(mapping.pk, exc.code, claim)
        logger.warning("matrix_provisioning_failed", extra={"error_code": exc.code})
        return False
    except Exception:
        _record_provision_failure(mapping.pk, "matrix_remote_unavailable", claim)
        logger.warning("matrix_provisioning_failed", extra={"error_code": "matrix_remote_unavailable"})
        return False

    revoke_after = False
    stale_remote_room = False
    current_generation_owns_room = False
    now = timezone.now()
    with transaction.atomic():
        locked = MatrixRoomMapping.objects.select_for_update().get(pk=mapping.pk)
        same_generation = (
            locked.chat_room_id == claim.chat_room_id
            and locked.membership_epoch == claim.membership_epoch
            and locked.room_alias_localpart == claim.room_alias_localpart
            and tuple(locked.member_matrix_localparts)
            == claim.member_matrix_localparts
        )
        same_claim = (
            same_generation
            and locked.attempts == claim.attempts
            and locked.locked_at == claim.locked_at
        )
        if locked.state == MatrixRoomMapping.State.REVOKE_PENDING and same_claim:
            revoke_after = True
            locked.matrix_room_id = room_result.room_id
            locked.matrix_room_alias = room_result.room_alias
            locked.locked_at = None
            locked.save(update_fields=[
                "matrix_room_id",
                "matrix_room_alias",
                "locked_at",
                "updated_at",
            ])
        elif locked.state == MatrixRoomMapping.State.PROVISIONING and same_claim:
            locked.matrix_room_id = room_result.room_id
            locked.matrix_room_alias = room_result.room_alias
            locked.locked_at = None
            if not authorized_rooms(room.user_one).filter(pk=room.pk).exists():
                # A relationship/block change can race the remote call. Keep
                # the discovered room id and revoke it immediately rather than
                # briefly publishing an ACTIVE mapping for stale membership.
                revoke_after = True
                locked.state = MatrixRoomMapping.State.REVOKE_PENDING
                locked.available_at = now
                locked.last_error_code = ""
                locked.save(update_fields=[
                    "matrix_room_id",
                    "matrix_room_alias",
                    "state",
                    "available_at",
                    "locked_at",
                    "last_error_code",
                    "updated_at",
                ])
            else:
                locked.state = MatrixRoomMapping.State.ACTIVE
                locked.replacement_pending = False
                locked.last_error_code = ""
                locked.provisioned_at = now
                locked.revoked_at = None
                locked.remote_verified_at = now
                locked.save(update_fields=[
                    "matrix_room_id",
                    "matrix_room_alias",
                    "state",
                    "replacement_pending",
                    "locked_at",
                    "last_error_code",
                    "provisioned_at",
                    "revoked_at",
                    "remote_verified_at",
                    "updated_at",
                ])
        elif (
            same_generation
            and locked.state == MatrixRoomMapping.State.PROVISIONING
        ):
            # A newer claim for the same deterministic alias owns completion.
            # Both calls resolve to the same room, so revoking A's result would
            # destroy B's valid in-flight room.
            current_generation_owns_room = True
        elif (
            same_generation
            and locked.state == MatrixRoomMapping.State.ACTIVE
            and locked.matrix_room_id == room_result.room_id
            and locked.matrix_room_alias == room_result.room_alias
        ):
            current_generation_owns_room = True
        else:
            # The remote create completed after this claim was superseded.
            # Never credit or overwrite the current generation; retain exact
            # cleanup independently so even a failed direct leave is retried.
            stale_remote_room = True
        for user_id, matrix_user_id in provisioned_users.items():
            MatrixUserMapping.objects.filter(user_id=user_id).update(
                matrix_user_id=matrix_user_id,
                state=MatrixUserMapping.State.ACTIVE,
                last_error_code="",
                provisioned_at=now,
                updated_at=now,
            )
    if stale_remote_room:
        _queue_remote_revocation(
            mapping.pk,
            room_id=room_result.room_id,
            room_alias=room_result.room_alias,
            member_localparts=claim.member_matrix_localparts,
            membership_epoch=claim.membership_epoch,
        )
        process_pending_remote_revocation(mapping.pk, service=matrix)
        return False
    if current_generation_owns_room:
        return True
    if revoke_after:
        revoke_matrix_room(mapping.pk, service=matrix)
        return False
    return True


def safe_provision_matrix_room(mapping_id: int) -> None:
    try:
        provision_matrix_room(mapping_id)
    except Exception:
        # on_commit callbacks must never turn a committed relationship into a
        # false request failure. The retry worker owns recovery.
        logger.warning("matrix_provisioning_callback_failed")


def schedule_matrix_room_provisioning(mapping: MatrixRoomMapping) -> None:
    transaction.on_commit(lambda: safe_provision_matrix_room(mapping.pk))


def mark_matrix_mapping_revoke_pending(mapping: MatrixRoomMapping) -> MatrixRoomMapping:
    with transaction.atomic():
        locked = MatrixRoomMapping.objects.select_for_update().get(pk=mapping.pk)
        now = timezone.now()
        provisioning_inflight = (
            locked.state == MatrixRoomMapping.State.PROVISIONING
            and locked.locked_at is not None
            and locked.locked_at
            > now - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
        )
        locked.state = MatrixRoomMapping.State.REVOKE_PENDING
        locked.replacement_pending = False
        locked.available_at = (
            locked.locked_at + timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
            if provisioning_inflight
            else now
        )
        if not provisioning_inflight:
            locked.locked_at = None
        locked.last_error_code = ""
        locked.save(update_fields=[
            "state",
            "replacement_pending",
            "available_at",
            "locked_at",
            "last_error_code",
            "updated_at",
        ])
        return locked


def mark_matrix_room_revoke_pending(room: ChatRoom) -> MatrixRoomMapping:
    return mark_matrix_mapping_revoke_pending(ensure_matrix_mapping_records(room))


def mark_matrix_room_replacement_pending(
    mapping: MatrixRoomMapping,
    *,
    error_code: str = "matrix_room_policy_mismatch",
    expected: MatrixRoomVerificationSnapshot | None = None,
) -> MatrixRoomMapping | None:
    with transaction.atomic():
        locked = MatrixRoomMapping.objects.select_for_update().get(pk=mapping.pk)
        if expected is not None and not _mapping_matches_verification_snapshot(
            locked,
            expected,
        ):
            return None
        room = locked.chat_room
        eligible = (
            room is not None
            and authorized_rooms(room.user_one).filter(pk=room.pk).exists()
        )
        now = timezone.now()
        provisioning_inflight = (
            locked.state == MatrixRoomMapping.State.PROVISIONING
            and locked.locked_at is not None
            and locked.locked_at
            > now - timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
        )
        locked.state = MatrixRoomMapping.State.REVOKE_PENDING
        locked.replacement_pending = eligible
        locked.available_at = (
            locked.locked_at + timedelta(seconds=settings.MATRIX_PROVISION_LOCK_SECONDS)
            if provisioning_inflight
            else now
        )
        if not provisioning_inflight:
            locked.locked_at = None
        locked.last_error_code = error_code[:40]
        locked.remote_verified_at = None
        locked.save(update_fields=[
            "state",
            "replacement_pending",
            "available_at",
            "locked_at",
            "last_error_code",
            "remote_verified_at",
            "updated_at",
        ])
        return locked


def _verification_snapshot(mapping: MatrixRoomMapping) -> MatrixRoomVerificationSnapshot:
    return MatrixRoomVerificationSnapshot(
        chat_room_id=mapping.chat_room_id,
        matrix_room_id=mapping.matrix_room_id,
        matrix_room_alias=mapping.matrix_room_alias,
        membership_epoch=mapping.membership_epoch,
        member_matrix_localparts=tuple(mapping.member_matrix_localparts),
    )


def _mapping_matches_verification_snapshot(
    mapping: MatrixRoomMapping,
    expected: MatrixRoomVerificationSnapshot,
) -> bool:
    return (
        mapping.state == MatrixRoomMapping.State.ACTIVE
        and not mapping.replacement_pending
        and mapping.chat_room_id == expected.chat_room_id
        and mapping.matrix_room_id == expected.matrix_room_id
        and mapping.matrix_room_alias == expected.matrix_room_alias
        and mapping.membership_epoch == expected.membership_epoch
        and tuple(mapping.member_matrix_localparts)
        == expected.member_matrix_localparts
    )


def _record_verification_error(
    mapping_id: int,
    expected: MatrixRoomVerificationSnapshot,
    code: str,
) -> None:
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if not _mapping_matches_verification_snapshot(mapping, expected):
            return
        mapping.last_error_code = code[:40]
        mapping.save(update_fields=["last_error_code", "updated_at"])


def verify_active_matrix_room(
    mapping_id: int,
    *,
    service=None,
    recover: bool = True,
) -> bool:
    mapping = MatrixRoomMapping.objects.select_related("chat_room").get(pk=mapping_id)
    if mapping.state != MatrixRoomMapping.State.ACTIVE:
        return False
    expected = _verification_snapshot(mapping)
    room = mapping.chat_room
    if (
        room is None
        or not authorized_rooms(room.user_one).filter(pk=room.pk).exists()
    ):
        queued = mark_matrix_room_replacement_pending(
            mapping,
            error_code="matrix_relationship_ineligible",
            expected=expected,
        )
        if queued is not None and recover:
            revoke_matrix_room(mapping_id, service=service)
        return False
    current_localparts = [
        matrix_localpart(user_id)
        for user_id in sorted([room.user_one_id, room.user_two_id])
    ]
    if mapping.member_matrix_localparts != current_localparts:
        queued = mark_matrix_room_replacement_pending(
            mapping,
            error_code="matrix_membership_changed",
            expected=expected,
        )
        if queued is not None and recover:
            revoke_matrix_room(mapping_id, service=service)
        return False
    if not mapping.matrix_room_id or not mapping.matrix_room_alias:
        queued = mark_matrix_room_replacement_pending(
            mapping,
            error_code="matrix_room_mismatch",
            expected=expected,
        )
        if queued is not None and recover:
            revoke_matrix_room(mapping_id, service=service)
        return False
    matrix = service or get_matrix_service()
    try:
        matrix.verify_room(
            room_id=mapping.matrix_room_id,
            room_alias=mapping.matrix_room_alias,
            member_user_ids=[
                _matrix_user_id(localpart) for localpart in current_localparts
            ],
        )
    except MatrixUnavailable as exc:
        _record_verification_error(mapping_id, expected, exc.code)
        return False
    except MatrixServiceError as exc:
        if exc.code in ROOM_DRIFT_ERROR_CODES:
            queued = mark_matrix_room_replacement_pending(
                mapping,
                error_code=exc.code,
                expected=expected,
            )
            if queued is not None and recover:
                revoke_matrix_room(mapping_id, service=matrix)
        else:
            _record_verification_error(mapping_id, expected, exc.code)
        return False
    except Exception:
        _record_verification_error(
            mapping_id,
            expected,
            "matrix_remote_unavailable",
        )
        return False
    with transaction.atomic():
        locked = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if not _mapping_matches_verification_snapshot(locked, expected):
            return False
        room = (
            ChatRoom.objects.select_for_update()
            .filter(pk=expected.chat_room_id)
            .first()
        )
        if room is None:
            return False
        current_localparts = tuple(
            matrix_localpart(user_id)
            for user_id in sorted([room.user_one_id, room.user_two_id])
        )
        if (
            current_localparts != expected.member_matrix_localparts
            or not authorized_rooms(room.user_one).filter(pk=room.pk).exists()
        ):
            return False
        now = timezone.now()
        locked.remote_verified_at = now
        locked.last_error_code = ""
        locked.save(update_fields=[
            "remote_verified_at",
            "last_error_code",
            "updated_at",
        ])
        return True


def _record_revoke_failure(
    mapping_id: int,
    code: str,
    *,
    expected_attempts: int,
    expected_locked_at: datetime,
    expected_epoch: int,
    expected_alias_localpart: str,
) -> None:
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if (
            mapping.state != MatrixRoomMapping.State.REVOKING
            or mapping.attempts != expected_attempts
            or mapping.locked_at != expected_locked_at
            or mapping.membership_epoch != expected_epoch
            or mapping.room_alias_localpart != expected_alias_localpart
        ):
            return
        mapping.state = MatrixRoomMapping.State.REVOKE_PENDING
        mapping.locked_at = None
        mapping.last_error_code = code[:40]
        mapping.available_at = _retry_at(mapping.attempts)
        mapping.save(update_fields=[
            "state",
            "locked_at",
            "last_error_code",
            "available_at",
            "updated_at",
        ])


def revoke_matrix_room(mapping_id: int, *, service=None) -> bool:
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if mapping.state == MatrixRoomMapping.State.REVOKED:
            return True
        if mapping.state not in {
            MatrixRoomMapping.State.REVOKE_PENDING,
            MatrixRoomMapping.State.REVOKING,
        }:
            return False
        stale_before = timezone.now() - timedelta(
            seconds=settings.MATRIX_PROVISION_LOCK_SECONDS
        )
        if (
            mapping.state in {
                MatrixRoomMapping.State.REVOKE_PENDING,
                MatrixRoomMapping.State.REVOKING,
            }
            and mapping.locked_at
            and mapping.locked_at > stale_before
        ):
            return False
        mapping.state = MatrixRoomMapping.State.REVOKING
        mapping.attempts += 1
        mapping.locked_at = timezone.now()
        mapping.save(update_fields=["state", "attempts", "locked_at", "updated_at"])
        claim_attempts = mapping.attempts
        claim_locked_at = mapping.locked_at
        claim_epoch = mapping.membership_epoch
        claim_alias_localpart = mapping.room_alias_localpart
        room_id = mapping.matrix_room_id
        member_localparts = list(mapping.member_matrix_localparts)
        if not member_localparts and mapping.chat_room is not None:
            member_localparts = [
                matrix_localpart(user_id)
                for user_id in sorted([
                    mapping.chat_room.user_one_id,
                    mapping.chat_room.user_two_id,
                ])
            ]
            mapping.member_matrix_localparts = member_localparts
            mapping.save(update_fields=["member_matrix_localparts", "updated_at"])
        room_alias_localpart = mapping.room_alias_localpart

    try:
        matrix = service or get_matrix_service()
        resolved_room_id = matrix.revoke_room(
            room_id=room_id,
            room_alias=_matrix_room_alias(room_alias_localpart),
            member_user_ids=[
                _matrix_user_id(localpart) for localpart in member_localparts
            ],
        )
    except MatrixServiceError as exc:
        _record_revoke_failure(
            mapping_id,
            exc.code,
            expected_attempts=claim_attempts,
            expected_locked_at=claim_locked_at,
            expected_epoch=claim_epoch,
            expected_alias_localpart=claim_alias_localpart,
        )
        logger.warning("matrix_revocation_failed", extra={"error_code": exc.code})
        return False
    except Exception:
        _record_revoke_failure(
            mapping_id,
            "matrix_remote_unavailable",
            expected_attempts=claim_attempts,
            expected_locked_at=claim_locked_at,
            expected_epoch=claim_epoch,
            expected_alias_localpart=claim_alias_localpart,
        )
        logger.warning("matrix_revocation_failed", extra={"error_code": "matrix_remote_unavailable"})
        return False
    with transaction.atomic():
        mapping = MatrixRoomMapping.objects.select_for_update().get(pk=mapping_id)
        if (
            mapping.state != MatrixRoomMapping.State.REVOKING
            or mapping.attempts != claim_attempts
            or mapping.locked_at != claim_locked_at
            or mapping.membership_epoch != claim_epoch
            or mapping.room_alias_localpart != claim_alias_localpart
        ):
            return False
        if resolved_room_id:
            mapping.matrix_room_id = resolved_room_id
        now = timezone.now()
        room = mapping.chat_room
        replace = (
            mapping.replacement_pending
            and room is not None
            and authorized_rooms(room.user_one).filter(pk=room.pk).exists()
        )
        if replace:
            new_epoch = mapping.membership_epoch + 1
            mapping.membership_epoch = new_epoch
            mapping.room_alias_localpart = matrix_room_alias_localpart(
                room.pk,
                new_epoch,
            )
            mapping.matrix_room_id = None
            mapping.matrix_room_alias = None
            mapping.member_matrix_localparts = [
                matrix_localpart(user_id)
                for user_id in sorted([room.user_one_id, room.user_two_id])
            ]
            mapping.state = MatrixRoomMapping.State.PENDING
            mapping.replacement_pending = False
            mapping.attempts = 0
            mapping.available_at = now
            mapping.locked_at = None
            mapping.last_error_code = ""
            mapping.provisioned_at = None
            mapping.revoked_at = now
            mapping.remote_verified_at = None
            mapping.save(update_fields=[
                "membership_epoch",
                "room_alias_localpart",
                "matrix_room_id",
                "matrix_room_alias",
                "member_matrix_localparts",
                "state",
                "replacement_pending",
                "attempts",
                "available_at",
                "locked_at",
                "last_error_code",
                "provisioned_at",
                "revoked_at",
                "remote_verified_at",
                "updated_at",
            ])
        else:
            mapping.state = MatrixRoomMapping.State.REVOKED
            mapping.replacement_pending = False
            mapping.revoked_at = now
            mapping.locked_at = None
            mapping.last_error_code = ""
            mapping.remote_verified_at = None
            mapping.save(update_fields=[
                "matrix_room_id",
                "state",
                "replacement_pending",
                "revoked_at",
                "locked_at",
                "last_error_code",
                "remote_verified_at",
                "updated_at",
            ])
    return True


def safe_revoke_matrix_room(mapping_id: int) -> None:
    try:
        revoke_matrix_room(mapping_id)
    except Exception:
        logger.warning("matrix_revocation_callback_failed")


def schedule_matrix_room_revocation(mapping: MatrixRoomMapping) -> None:
    transaction.on_commit(lambda: safe_revoke_matrix_room(mapping.pk))


def matrix_bootstrap_result_is_current(room_id, user: UserModel, data: dict) -> bool:
    """Bind a just-issued login token to the still-current room generation."""
    room = authorized_rooms(user).filter(pk=room_id).first()
    if room is None:
        return False
    mapping = MatrixRoomMapping.objects.filter(
        chat_room_id=room.pk,
        state=MatrixRoomMapping.State.ACTIVE,
    ).first()
    if mapping is None:
        return False
    current_localparts = [
        matrix_localpart(user_id)
        for user_id in sorted([room.user_one_id, room.user_two_id])
    ]
    return (
        not mapping.replacement_pending
        and not mapping.pending_remote_revocations
        and mapping.member_matrix_localparts == current_localparts
        and mapping.matrix_room_id == data.get("room_id")
        and mapping.matrix_room_alias == data.get("room_alias")
        and mapping.membership_epoch == data.get("membership_epoch")
    )


def matrix_bootstrap(room: ChatRoom, user: UserModel, *, django_session_id: str, service=None):
    mapping = ensure_matrix_mapping_records(room)
    if mapping.pending_remote_revocations:
        process_pending_remote_revocation(mapping.pk, service=service)
        mapping.refresh_from_db()
        if mapping.pending_remote_revocations:
            raise MatrixUnavailable("matrix_revocation_pending")
    if mapping.state == MatrixRoomMapping.State.ACTIVE:
        verified = verify_active_matrix_room(
            mapping.pk,
            service=service,
            recover=True,
        )
        mapping.refresh_from_db()
        if not verified and mapping.state == MatrixRoomMapping.State.ACTIVE:
            raise MatrixUnavailable(mapping.last_error_code or "matrix_room_verification_failed")
    if mapping.state != MatrixRoomMapping.State.ACTIVE:
        provision_matrix_room(mapping.pk, service=service)
        mapping.refresh_from_db()
    if mapping.state != MatrixRoomMapping.State.ACTIVE or not mapping.matrix_room_id:
        raise MatrixUnavailable(mapping.last_error_code or "matrix_room_pending")
    identity = MatrixUserMapping.objects.filter(user=user).first()
    if identity is None or identity.state != MatrixUserMapping.State.ACTIVE:
        raise MatrixUnavailable("matrix_identity_pending")
    user_ids = sorted([room.user_one_id, room.user_two_id])
    partner_id = user_ids[1] if user.pk == user_ids[0] else user_ids[0]
    partner = MatrixUserMapping.objects.filter(user_id=partner_id).first()
    if partner is None or partner.state != MatrixUserMapping.State.ACTIVE:
        raise MatrixUnavailable("matrix_partner_pending")
    matrix = service or get_matrix_service()
    session = matrix.issue_login_token(
        matrix_user_id=identity.matrix_user_id,
        django_session_id=django_session_id,
        ttl_seconds=settings.MATRIX_SESSION_TTL_SECONDS,
    )
    if not session.login_token or session.expires_at <= timezone.now():
        raise MatrixUnavailable("matrix_session_invalid")
    return {
        "protocol_id": settings.MATRIX_PROTOCOL_ID,
        "homeserver_url": settings.MATRIX_HOMESERVER_URL,
        "room_id": mapping.matrix_room_id,
        "room_alias": mapping.matrix_room_alias,
        "membership_epoch": mapping.membership_epoch,
        "relationship_id": room.couple.couple_connection_id,
        "self_user_id": user.pk,
        "self_matrix_user_id": identity.matrix_user_id,
        "partner_user_id": partner_id,
        "partner_matrix_user_id": partner.matrix_user_id,
        "initiator_user_id": user_ids[0],
        "login_type": session.login_type,
        "login_token": session.login_token,
        "login_token_expires_at": session.expires_at.isoformat(),
        "login_token_one_time": False,
        "refresh_token_enabled": False,
        "access_token_expires_in_seconds": settings.MATRIX_ACCESS_TOKEN_LIFETIME_SECONDS,
    }
