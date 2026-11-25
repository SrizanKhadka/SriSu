from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.management.commands.seed_local_test_accounts import USERS
from authentication.models import UserInterestModel, UserModel
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping
from social.models import CoupleConnectionModel, CoupleMembershipModel, CoupleModel
from utils.choices import CoupleConnectionStatus


@override_settings(DEBUG=True, OTP_MOCK_DELIVERY=True, OTP_MOCK_CODE="123456")
class LocalTestAccountCommandTests(TestCase):
    def run_seed(self):
        output = StringIO()
        call_command(
            "seed_local_test_accounts",
            confirm_local=True,
            stdout=output,
        )
        return output.getvalue()

    def test_requires_explicit_local_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--confirm-local"):
            call_command("seed_local_test_accounts", stdout=StringIO())

        self.assertFalse(UserModel.objects.exists())

    @override_settings(DEBUG=False)
    def test_refuses_non_debug_environment(self):
        with self.assertRaisesMessage(CommandError, "only runs with DEBUG=true"):
            self.run_seed()

        self.assertFalse(UserModel.objects.exists())

    @override_settings(OTP_MOCK_DELIVERY=False)
    def test_refuses_real_otp_delivery_environment(self):
        with self.assertRaisesMessage(CommandError, "OTP mock delivery enabled"):
            self.run_seed()

        self.assertFalse(UserModel.objects.exists())

    @override_settings(OTP_MOCK_CODE="")
    def test_refuses_missing_mock_code(self):
        with self.assertRaisesMessage(CommandError, "valid OTP mock code"):
            self.run_seed()

        self.assertFalse(UserModel.objects.exists())

    @override_settings(OTP_MOCK_CODE="not-six-digits")
    def test_refuses_malformed_mock_code(self):
        with self.assertRaisesMessage(CommandError, "exactly six ASCII digits"):
            self.run_seed()

        self.assertFalse(UserModel.objects.exists())

    def test_creates_complete_scenarios_without_exposing_mock_code(self):
        output = self.run_seed()
        phones = [definition["phone_number"] for definition in USERS]
        users = UserModel.objects.filter(phone_number__in=phones)

        self.assertEqual(users.count(), 6)
        self.assertTrue(all(user.is_active for user in users))
        self.assertTrue(all(user.is_phone_verified for user in users))
        self.assertTrue(all(user.is_profile_complete for user in users))
        self.assertTrue(all(user.profile_photo_skipped for user in users))
        self.assertTrue(all(not user.has_usable_password() for user in users))
        self.assertEqual(UserInterestModel.objects.filter(user__in=users).count(), 18)

        self.assertEqual(CoupleModel.objects.count(), 2)
        self.assertEqual(CoupleMembershipModel.objects.count(), 4)
        self.assertEqual(ChatRoom.objects.count(), 2)
        self.assertEqual(MatrixRoomMapping.objects.count(), 2)
        self.assertEqual(MatrixUserMapping.objects.count(), 4)
        self.assertEqual(
            CoupleConnectionModel.objects.filter(
                connection_status=CoupleConnectionStatus.ACCEPTED,
                ended_at__isnull=True,
            ).count(),
            2,
        )
        self.assertEqual(
            CoupleConnectionModel.objects.filter(
                connection_status=CoupleConnectionStatus.PENDING,
            ).count(),
            1,
        )

        unpaired_phones = {"+9779800000105", "+9779800000106"}
        self.assertFalse(
            CoupleMembershipModel.objects.filter(
                user__phone_number__in=unpaired_phones
            ).exists()
        )
        self.assertIn("+9779800000101", output)
        self.assertNotIn("123456", output)
        self.assertIn("secure_chat_active=0/2", output)
        self.assertIn("Secure chat is not ready", output)

    def test_rerun_is_idempotent_and_preserves_profile_edits(self):
        first_output = self.run_seed()
        asha = UserModel.objects.get(phone_number="+9779800000101")
        asha.full_name = "Asha's edited local name"
        asha.save(update_fields=["full_name", "updated_date"])

        second_output = self.run_seed()
        asha.refresh_from_db()

        self.assertIn("created_users=6", first_output)
        self.assertIn("created_couples=2", first_output)
        self.assertIn("created_users=0", second_output)
        self.assertIn("created_couples=0", second_output)
        self.assertEqual(asha.full_name, "Asha's edited local name")
        self.assertEqual(UserModel.objects.count(), 6)
        self.assertEqual(CoupleModel.objects.count(), 2)
        self.assertEqual(CoupleMembershipModel.objects.count(), 4)
        self.assertEqual(ChatRoom.objects.count(), 2)
        self.assertEqual(MatrixRoomMapping.objects.count(), 2)
        self.assertEqual(MatrixUserMapping.objects.count(), 4)

    def test_seeded_account_can_sign_in_through_mock_otp(self):
        self.run_seed()
        client = APIClient()
        client.credentials(
            HTTP_X_SRISU_CONTRACT="core-1",
            HTTP_X_SRISU_AUTH="auth-1",
        )
        phone_number = "+9779800000101"

        issued = client.post(
            "/api/auth/send-otp/",
            {
                "phone_number": phone_number,
                "request_id": str(uuid4()),
            },
            format="json",
        )
        self.assertEqual(issued.status_code, 200)

        verified = client.post(
            "/api/auth/verify-otp/",
            {
                "phone_number": phone_number,
                "challenge_id": issued.data["data"]["challenge_id"],
                "otp_code": "123456",
            },
            format="json",
        )

        self.assertEqual(verified.status_code, 200)
        self.assertEqual(
            verified.data["data"]["user"]["phone_number"],
            phone_number,
        )
        self.assertTrue(verified.data["data"]["progress"]["profile_complete"])

    def test_rejected_pending_scenario_is_preserved_and_reported(self):
        self.run_seed()
        invitation = CoupleConnectionModel.objects.get(
            sender_number="+9779800000106",
            receiver_number="+9779800000105",
        )
        invitation.connection_status = CoupleConnectionStatus.REJECTED
        invitation.save(update_fields=["connection_status", "updated_at"])

        output = self.run_seed()

        invitation.refresh_from_db()
        self.assertEqual(
            invitation.connection_status,
            CoupleConnectionStatus.REJECTED,
        )
        self.assertIn("pending_pair=rejected", output)
        self.assertFalse(
            CoupleConnectionModel.objects.filter(
                sender_number="+9779800000106",
                receiver_number="+9779800000105",
                connection_status=CoupleConnectionStatus.PENDING,
            ).exists()
        )

    def test_reserved_phone_is_never_overwritten(self):
        existing = UserModel.objects.create_user(
            phone_number="+9779800000101",
            username="someone_else",
            full_name="Existing local owner",
        )

        with self.assertRaisesMessage(CommandError, "belongs to another local identity"):
            self.run_seed()

        existing.refresh_from_db()
        self.assertEqual(existing.username, "someone_else")
        self.assertEqual(existing.full_name, "Existing local owner")
        self.assertEqual(UserModel.objects.count(), 1)

    def test_reserved_username_is_never_reassigned(self):
        existing = UserModel.objects.create_user(
            phone_number="+9779800099999",
            username="asha_local_01",
            full_name="Existing username owner",
        )

        with self.assertRaisesMessage(CommandError, "belongs to another account"):
            self.run_seed()

        existing.refresh_from_db()
        self.assertEqual(existing.phone_number, "+9779800099999")
        self.assertEqual(existing.full_name, "Existing username owner")
        self.assertEqual(UserModel.objects.count(), 1)
