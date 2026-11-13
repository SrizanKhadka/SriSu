from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import UserModel
from chat.models import ChatRoom
from social.models import (
    CoupleConnectionModel,
    CoupleMembershipModel,
    CoupleModel,
)
from social.services.couple_profile_service import create_or_get_couple_for_connection
from social.services.relationship_service import accept_connection, end_connection
from social.api.views import PartnerDiscoveryThrottle
from utils.choices import CoupleConnectionStatus


class CoupleProfileAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("couple-profile")
        self.user = self.create_user("+9779800000001", "Sri")
        self.partner = self.create_user("+9779800000002", "Su")

    @staticmethod
    def create_user(phone_number, full_name, **extra_fields):
        return UserModel.objects.create_user(
            phone_number=phone_number,
            full_name=full_name,
            **extra_fields,
        )

    @staticmethod
    def accept_connection(user_one, user_two):
        return CoupleConnectionModel.objects.create(
            sender_number=user_one.phone_number,
            receiver_number=user_two.phone_number,
            connection_status=CoupleConnectionStatus.ACCEPTED,
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def valid_payload(self, **overrides):
        payload = {
            "partner_id": self.partner.id,
            "title": "The Soulmates",
            "anniversary_date": "2024-01-12",
            "shared_dreams": ["See the northern lights", "Build a home"],
            "shared_interests": ["Travel", "Coffee", "Photography"],
            "relationship_tagline": "Two hearts, one journey.",
            "journey_story": "It started with a chance encounter.",
            "relationship_strength": 98,
        }
        payload.update(overrides)
        return payload

    def create_profile(self, **overrides):
        self.accept_connection(self.user, self.partner)
        self.authenticate()
        return self.client.post(self.url, self.valid_payload(**overrides), format="json")

    def test_create_profile_for_accepted_active_users(self):
        response = self.create_profile()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CoupleModel.objects.count(), 1)
        couple = CoupleModel.objects.get()
        self.assertEqual(couple.memberships.count(), 2)
        self.assertIsNotNone(couple.profile_completed_at)
        self.assertEqual(response.data["data"]["couple_profile"]["partner"]["id"], self.partner.id)
        self.assertEqual(len(response.data["data"]["couple_profile"]["members"]), 2)
        self.assertTrue(response.data["data"]["couple_profile"]["profile_complete"])

    def test_accepting_connection_creates_memberships_and_couple_chat(self):
        connection = CoupleConnectionModel.objects.create(
            sender_number=self.user.phone_number,
            receiver_number=self.partner.phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        self.authenticate(self.partner)

        response = self.client.put(
            reverse("coupleConnectionView-detail", args=[connection.id]),
            {
                "sender_number": self.user.phone_number,
                "receiver_number": self.partner.phone_number,
                "connection_status": CoupleConnectionStatus.ACCEPTED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        couple = CoupleModel.objects.get(couple_connection=connection)
        self.assertEqual(couple.memberships.count(), 2)
        self.assertTrue(
            ChatRoom.objects.filter(
                couple=couple,
                user_one__in=[self.user, self.partner],
                user_two__in=[self.user, self.partner],
            ).exists()
        )

    def test_create_profile_allows_optional_fields_to_be_omitted(self):
        self.accept_connection(self.user, self.partner)
        self.authenticate()

        response = self.client.post(
            self.url,
            {"partner_id": self.partner.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        profile = response.data["data"]["couple_profile"]
        self.assertEqual(profile["shared_dreams"], [])
        self.assertEqual(profile["shared_interests"], [])
        self.assertIsNone(profile["anniversary_date"])

    def test_get_returns_authenticated_users_profile(self):
        self.create_profile()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile = response.data["data"]["couple_profile"]
        self.assertEqual(profile["title"], "The Soulmates")
        self.assertEqual(profile["partner"]["id"], self.partner.id)
        self.assertGreaterEqual(profile["days_together"], 0)

    def test_either_member_can_partially_update_shared_profile(self):
        self.create_profile()
        self.authenticate(self.partner)

        response = self.client.patch(
            self.url,
            {
                "relationship_tagline": "Still choosing each other.",
                "shared_interests": ["Travel", "Travel", "  Coffee  "],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile = response.data["data"]["couple_profile"]
        self.assertEqual(profile["relationship_tagline"], "Still choosing each other.")
        self.assertEqual(profile["shared_interests"], ["Travel", "Coffee"])
        self.assertEqual(profile["title"], "The Soulmates")

    def test_legacy_update_route_is_preserved_and_member_scoped(self):
        self.create_profile()
        couple = CoupleModel.objects.get()
        legacy_url = reverse("updateCoupleView-detail", args=[couple.id])

        member_response = self.client.patch(
            legacy_url,
            {"title": "Our Story"},
            format="json",
        )

        stranger = self.create_user("+9779800000006", "Stranger")
        self.authenticate(stranger)
        stranger_response = self.client.patch(
            legacy_url,
            {"title": "Not yours"},
            format="json",
        )

        self.assertEqual(member_response.status_code, status.HTTP_200_OK)
        self.assertEqual(stranger_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(CoupleModel.objects.get().title, "Our Story")

    def test_duplicate_profile_creation_returns_conflict(self):
        self.create_profile()

        response = self.client.post(self.url, self.valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(CoupleModel.objects.count(), 1)
        self.assertEqual(CoupleMembershipModel.objects.count(), 2)

    def test_create_rejects_self_as_partner(self):
        self.authenticate()

        response = self.client.post(
            self.url,
            {"partner_id": self.user.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CoupleModel.objects.count(), 0)

    def test_create_requires_an_accepted_connection(self):
        CoupleConnectionModel.objects.create(
            sender_number=self.user.phone_number,
            receiver_number=self.partner.phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        self.authenticate()

        response = self.client.post(self.url, self.valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CoupleModel.objects.count(), 0)

    def test_create_rejects_deactivated_partner(self):
        self.partner.is_active = False
        self.partner.save(update_fields=["is_active"])
        self.authenticate()

        response = self.client.post(self.url, self.valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CoupleModel.objects.count(), 0)

    def test_user_cannot_belong_to_a_second_couple(self):
        first_connection = self.accept_connection(self.user, self.partner)
        create_or_get_couple_for_connection(first_connection)
        another_partner = self.create_user("+9779800000003", "Another")
        self.accept_connection(self.user, another_partner)
        self.authenticate()

        response = self.client.post(
            self.url,
            self.valid_payload(partner_id=another_partner.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CoupleModel.objects.count(), 1)
        self.assertEqual(CoupleMembershipModel.objects.count(), 2)

    def test_non_member_cannot_read_or_update_another_profile(self):
        self.create_profile()
        stranger = self.create_user("+9779800000004", "Stranger")
        self.authenticate(stranger)

        get_response = self.client.get(self.url)
        patch_response = self.client.patch(
            self.url,
            {"title": "Not yours"},
            format="json",
        )

        self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(patch_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(CoupleModel.objects.get().title, "The Soulmates")

    def test_update_cannot_change_partner(self):
        self.create_profile()
        another_user = self.create_user("+9779800000005", "Another")

        response = self.client.patch(
            self.url,
            {"partner_id": another_user.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(CoupleMembershipModel.objects.filter(user=another_user).exists())

    def test_invalid_profile_values_are_rejected(self):
        self.accept_connection(self.user, self.partner)
        self.authenticate()

        future_date = (date.today() + timedelta(days=1)).isoformat()
        response = self.client.post(
            self.url,
            self.valid_payload(
                anniversary_date=future_date,
                shared_interests="Travel",
                relationship_strength=101,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CoupleModel.objects.count(), 0)

    def test_authentication_is_required(self):
        self.client.force_authenticate(user=None)

        get_response = self.client.get(self.url)
        post_response = self.client.post(self.url, self.valid_payload(), format="json")
        patch_response = self.client.patch(self.url, {"title": "No access"}, format="json")

        self.assertEqual(get_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(post_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(patch_response.status_code, status.HTTP_401_UNAUTHORIZED)


class CoupleConnectionSecurityTests(APITestCase):
    def setUp(self):
        self.sender = UserModel.objects.create_user(
            phone_number="+9779810000001", full_name="Synthetic Sender"
        )
        self.receiver = UserModel.objects.create_user(
            phone_number="+9779810000002", full_name="Synthetic Receiver"
        )
        self.outsider = UserModel.objects.create_user(
            phone_number="+9779810000003", full_name="Synthetic Outsider"
        )

    def _payload(self):
        return {
            "sender_number": self.sender.phone_number,
            "receiver_number": self.receiver.phone_number,
        }

    def test_exact_phone_partner_lookup_returns_only_the_preview_contract(self):
        self.receiver.email = "private@example.test"
        self.receiver.dob = date(1990, 1, 1)
        self.receiver.city = "Private City"
        self.receiver.country = "Private Country"
        self.receiver.bio = "Private biography"
        self.receiver.save(
            update_fields=["email", "dob", "city", "country", "bio"]
        )
        self.client.force_authenticate(self.sender)

        response = self.client.get(
            reverse("find-partner"),
            {"phone_number": self.receiver.phone_number},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data["data"]),
            {"id", "full_name", "username", "phone_number", "profile_photo"},
        )
        self.assertEqual(response.data["data"]["phone_number"], self.receiver.phone_number)
        self.assertNotIn("private@example.test", str(response.data))
        self.assertNotIn("Private City", str(response.data))
        self.assertNotIn("Private biography", str(response.data))

    def test_exact_phone_partner_lookup_has_a_per_user_rate_budget(self):
        self.client.force_authenticate(self.sender)
        cache.clear()
        try:
            with patch.dict(
                PartnerDiscoveryThrottle.THROTTLE_RATES,
                {"partner_discovery": "1/min"},
                clear=False,
            ):
                first = self.client.get(
                    reverse("find-partner"),
                    {"phone_number": self.receiver.phone_number},
                )
                throttled = self.client.get(
                    reverse("find-partner"),
                    {"phone_number": self.receiver.phone_number},
                )
        finally:
            cache.clear()
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_invite_replays_idempotently_without_exposing_operation_key(self):
        self.receiver.email = "private@example.test"
        self.receiver.dob = date(1990, 1, 1)
        self.receiver.city = "Private City"
        self.receiver.country = "Private Country"
        self.receiver.save(update_fields=["email", "dob", "city", "country"])
        self.client.force_authenticate(self.sender)
        operation_id = uuid4()
        first = self.client.post(
            reverse("coupleConnectionView-list"),
            self._payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(operation_id),
        )
        replay = self.client.post(
            reverse("coupleConnectionView-list"),
            self._payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(operation_id),
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertTrue(replay.data["data"]["replayed"])
        self.assertEqual(CoupleConnectionModel.objects.count(), 1)
        self.assertNotIn("request_operation_id", str(first.data))
        self.assertNotIn("acceptance_operation_id", str(first.data))
        self.assertEqual(
            set(first.data["data"]["partner"]),
            {"id", "full_name", "username", "profile_photo"},
        )
        self.assertNotIn("private@example.test", str(first.data))
        self.assertNotIn("Private City", str(first.data))

    def test_unrelated_user_cannot_list_retrieve_or_delete_relationships(self):
        connection = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
            request_operation_id=uuid4(),
        )
        self.client.force_authenticate(self.outsider)

        connect_list = self.client.get(reverse("coupleConnectionView-list"))
        connect_detail = self.client.get(
            reverse("coupleConnectionView-detail", args=[connection.pk])
        )
        connect_delete = self.client.delete(
            reverse("coupleConnectionView-detail", args=[connection.pk])
        )
        request_list = self.client.get(reverse("coupleConnectionRequestView-list"))
        request_detail = self.client.get(
            reverse("coupleConnectionRequestView-detail", args=[connection.pk])
        )
        request_delete = self.client.delete(
            reverse("coupleConnectionRequestView-detail", args=[connection.pk])
        )

        self.assertEqual(connect_list.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.sender.phone_number, str(connect_list.data))
        self.assertEqual(connect_detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(connect_delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertNotIn(self.sender.phone_number, str(request_list.data))
        self.assertEqual(request_detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(request_delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(CoupleConnectionModel.objects.filter(pk=connection.pk).exists())

    def test_both_partners_discover_the_same_accepted_room(self):
        connection = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
        )
        accepted = accept_connection(connection_id=connection.pk, actor=self.receiver)
        responses = []
        for user in (self.sender, self.receiver):
            self.client.force_authenticate(user)
            responses.append(
                self.client.get(reverse("have-couple-connection-requested"))
            )

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertFalse(response.data["data"]["connection_requested"])
            self.assertEqual(
                response.data["data"]["connection"]["chat_room_id"],
                str(accepted.chat_room.pk),
            )
            self.assertEqual(
                response.data["data"]["connection"]["couple_id"],
                accepted.couple.pk,
            )

    def test_acceptance_and_end_do_not_depend_on_legacy_transport_hints(self):
        connection = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
        )
        accepted = accept_connection(connection_id=connection.pk, actor=self.receiver)
        ended = end_connection(connection_id=connection.pk, actor=self.sender)

        self.assertEqual(accepted.connection.pk, connection.pk)
        self.assertEqual(accepted.chat_room.couple_id, accepted.couple.pk)
        self.assertEqual(ended.connection_status, CoupleConnectionStatus.BREAKUP)
        self.assertEqual(ended.pk, connection.pk)

    def test_acceptance_invalidates_other_stale_pending_requests(self):
        stale = CoupleConnectionModel.objects.create(
            sender_number=self.sender.phone_number,
            receiver_number=self.outsider.phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        current = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
        )

        accept_connection(connection_id=current.pk, actor=self.receiver)

        stale.refresh_from_db()
        self.assertEqual(stale.connection_status, CoupleConnectionStatus.NOTHING)

    def test_reverse_pending_request_is_a_conflict_not_a_false_replay(self):
        CoupleConnectionModel.objects.create(
            sender_number=self.receiver.phone_number,
            receiver_number=self.sender.phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
            request_operation_id=uuid4(),
        )
        self.client.force_authenticate(self.sender)
        response = self.client.post(
            reverse("coupleConnectionView-list"),
            self._payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(CoupleConnectionModel.objects.count(), 1)

    def test_active_member_can_end_when_partner_account_is_inactive(self):
        connection = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
        )
        accepted = accept_connection(connection_id=connection.pk, actor=self.receiver)
        self.receiver.is_active = False
        self.receiver.save(update_fields=["is_active"])

        ended = end_connection(connection_id=connection.pk, actor=self.sender)

        self.assertEqual(ended.connection_status, CoupleConnectionStatus.BREAKUP)
        self.assertFalse(
            CoupleMembershipModel.objects.filter(couple=accepted.couple).exists()
        )
        self.assertEqual(
            CoupleMembershipModel.all_objects.filter(
                couple=accepted.couple,
                ended_at__isnull=False,
            ).count(),
            2,
        )

    def test_reject_and_cancel_retries_are_desired_state_idempotent(self):
        rejected = CoupleConnectionModel.objects.create(
            **self._payload(),
            connection_status=CoupleConnectionStatus.PENDING,
        )
        reject_payload = {
            **self._payload(),
            "connection_status": CoupleConnectionStatus.REJECTED,
        }
        operation_id = str(uuid4())
        self.client.force_authenticate(self.receiver)
        first_reject = self.client.put(
            reverse("coupleConnectionView-detail", args=[rejected.pk]),
            reject_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=operation_id,
        )
        replay_reject = self.client.put(
            reverse("coupleConnectionView-detail", args=[rejected.pk]),
            reject_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=operation_id,
        )
        self.assertEqual(first_reject.status_code, status.HTTP_200_OK)
        self.assertEqual(replay_reject.status_code, status.HTTP_200_OK)
        self.assertFalse(first_reject.data["data"]["replayed"])
        self.assertTrue(replay_reject.data["data"]["replayed"])

        cancelled = CoupleConnectionModel.objects.create(
            sender_number=self.sender.phone_number,
            receiver_number=self.outsider.phone_number,
            connection_status=CoupleConnectionStatus.PENDING,
        )
        cancel_payload = {
            "sender_number": self.sender.phone_number,
            "receiver_number": self.outsider.phone_number,
            "connection_status": CoupleConnectionStatus.NOTHING,
        }
        operation_id = str(uuid4())
        self.client.force_authenticate(self.sender)
        first_cancel = self.client.put(
            reverse("coupleConnectionView-detail", args=[cancelled.pk]),
            cancel_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=operation_id,
        )
        replay_cancel = self.client.put(
            reverse("coupleConnectionView-detail", args=[cancelled.pk]),
            cancel_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=operation_id,
        )
        self.assertEqual(first_cancel.status_code, status.HTTP_200_OK)
        self.assertEqual(replay_cancel.status_code, status.HTTP_200_OK)
        self.assertFalse(first_cancel.data["data"]["replayed"])
        self.assertTrue(replay_cancel.data["data"]["replayed"])
