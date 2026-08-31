from datetime import date, timedelta

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
