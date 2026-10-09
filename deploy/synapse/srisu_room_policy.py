"""Fail-closed room lifecycle policy for SriSu's private Synapse instance."""

from __future__ import annotations

import json
import re

try:  # These imports exist inside the pinned Synapse image.
    from synapse.api.errors import Codes
    from synapse.module_api import NOT_SPAM
except ModuleNotFoundError:  # Allow dependency-free policy unit tests.
    class Codes:  # pragma: no cover - the test/runtime fallback is trivial
        FORBIDDEN = "M_FORBIDDEN"

    NOT_SPAM = "NOT_SPAM"

try:
    from twisted.web.resource import Resource
except ModuleNotFoundError:  # pragma: no cover - dependency-free unit tests
    class Resource:
        isLeaf = True


POLICY_VERSION = "srisu-room-policy-v1"
POLICY_ENDPOINT = "/_synapse/client/srisu/policy/v1"


class PolicyVersionResource(Resource):
    isLeaf = True

    def __init__(self, *, server_name: str, provisioning_user_id: str):
        super().__init__()
        self.payload = json.dumps(
            {
                "policy_version": POLICY_VERSION,
                "server_name": server_name,
                "provisioning_user_id": provisioning_user_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def render_GET(self, request):
        request.setHeader(b"content-type", b"application/json")
        request.setHeader(b"cache-control", b"no-store")
        return self.payload


class SriSuRoomPolicy:
    """Allow only the Django-controlled provisioner to manage room topology."""

    def __init__(self, config: dict, api):
        self.server_name = config["server_name"]
        self.provisioning_localpart = config["provisioning_localpart"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", self.server_name):
            raise ValueError("SriSu Synapse policy server_name is invalid")
        if not re.fullmatch(r"[a-z0-9._=-]{1,64}", self.provisioning_localpart):
            raise ValueError("SriSu Synapse provisioning_localpart is invalid")
        self.provisioning_user_id = (
            f"@{self.provisioning_localpart}:{self.server_name}"
        )
        escaped_server = re.escape(self.server_name)
        self.alias_pattern = re.compile(
            rf"^#srisu_chat_[0-9a-f]{{32}}_e[1-9][0-9]*:{escaped_server}$"
        )
        self.alias_localpart_pattern = re.compile(
            r"^srisu_chat_[0-9a-f]{32}_e[1-9][0-9]*$"
        )
        self.member_pattern = re.compile(
            rf"^@srisu_user_[1-9][0-9]*:{escaped_server}$"
        )
        api.register_spam_checker_callbacks(
            user_may_create_room=self.user_may_create_room,
            user_may_create_room_alias=self.user_may_create_room_alias,
            user_may_invite=self.user_may_invite,
            user_may_send_3pid_invite=self.user_may_send_3pid_invite,
            user_may_publish_room=self.user_may_publish_room,
        )
        api.register_web_resource(
            POLICY_ENDPOINT,
            PolicyVersionResource(
                server_name=self.server_name,
                provisioning_user_id=self.provisioning_user_id,
            ),
        )

    @staticmethod
    def parse_config(config: dict) -> dict:
        return dict(config)

    def _decision(self, allowed: bool):
        return NOT_SPAM if allowed else Codes.FORBIDDEN

    async def user_may_create_room(self, user_id: str, room_config: dict):
        invited = room_config.get("invite")
        alias_localpart = room_config.get("room_alias_name")
        allowed = (
            user_id == self.provisioning_user_id
            and room_config.get("visibility") == "private"
            and room_config.get("room_version") == "11"
            and isinstance(alias_localpart, str)
            and self.alias_localpart_pattern.fullmatch(alias_localpart) is not None
            and isinstance(invited, list)
            and len(invited) == 2
            and len(set(invited)) == 2
            and all(
                isinstance(member, str)
                and self.member_pattern.fullmatch(member) is not None
                for member in invited
            )
        )
        return self._decision(allowed)

    async def user_may_create_room_alias(self, user_id: str, room_alias):
        alias = (
            room_alias.to_string()
            if hasattr(room_alias, "to_string")
            else str(room_alias)
        )
        return self._decision(
            user_id == self.provisioning_user_id
            and self.alias_pattern.fullmatch(alias) is not None
        )

    async def user_may_invite(
        self,
        inviter: str,
        invitee: str,
        room_id: str,
    ):
        del room_id
        return self._decision(
            inviter == self.provisioning_user_id
            and self.member_pattern.fullmatch(invitee) is not None
        )

    async def user_may_send_3pid_invite(
        self,
        inviter: str,
        medium: str,
        address: str,
        room_id: str,
    ):
        del inviter, medium, address, room_id
        return Codes.FORBIDDEN

    async def user_may_publish_room(self, user_id: str, room_id: str):
        del user_id, room_id
        return Codes.FORBIDDEN
