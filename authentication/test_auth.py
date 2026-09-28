"""All SMS is mocked; synthetic data only. PostgreSQL races are explicit tests."""
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from io import BytesIO
from unittest.mock import patch
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature, override_settings
from django.utils.timezone import now
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework.exceptions import ValidationError
from authentication.models import UserModel, OtpModel, DeviceSession, UserPhotoAlbumModel
from authentication.otp import request_code, verify_code
from authentication.sessions import create_session, rotate_refresh

PHONE = "+15005550101"


class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_X_SRISU_CONTRACT="core-1", HTTP_X_SRISU_AUTH="auth-1")
        self.clock = now()
        self.time = patch("authentication.otp.now", return_value=self.clock)
        self.time.start()
        self.addCleanup(self.time.stop)
        self.sender = patch("authentication.otp.deliver")
        self.deliver = self.sender.start()
        self.addCleanup(self.sender.stop)

    def issue(self):
        result = self.client.post("/api/auth/send-otp/", {"phone_number": PHONE, "request_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        from tools.check_core_contracts import validate_contract
        validate_contract("authChallenge", result.data)
        return result.data["data"], self.deliver.call_args.args[1]

    def verify(self, challenge, code):
        return self.client.post("/api/auth/verify-otp/", {"phone_number": PHONE, "challenge_id": challenge["challenge_id"], "otp_code": code}, format="json")

    def login(self):
        challenge, code = self.issue()
        result = self.verify(challenge, code)
        self.assertEqual(result.status_code, 200, result.data)
        from tools.check_core_contracts import validate_contract
        validate_contract("authVerified", result.data)
        tokens = result.data["data"]["tokens"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"], HTTP_X_SRISU_CONTRACT="core-1", HTTP_X_SRISU_AUTH="auth-1")
        return tokens

    def test_guest_can_browse_catalogue_but_cannot_read_or_write_private_space(self):
        response = self.client.get("/api/auth/interests/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["data"]["interests"], list)
        for path in (
            "/api/auth/setup-profile/", "/api/social/couple-profile/",
            "/api/social/couple-feed/", "/api/social/couple-moments/",
            "/api/social/couple-connection/", "/api/social/user-suggestions/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.patch("/api/auth/setup-profile/", {
            "full_name": "Guest", "username": "guest",
        }, format="json").status_code, 401)
        self.assertEqual(self.client.post("/api/social/connect-couple/", {
            "receiver_number": "+15005550102",
        }, format="json").status_code, 401)
        self.assertFalse(UserModel.objects.exists())
        self.assertFalse(DeviceSession.objects.exists())
        self.deliver.assert_not_called()

    def test_proof_protected_stable_identity_single_use(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True)
        challenge, code = self.issue()
        user.refresh_from_db()
        self.assertTrue(user.is_phone_verified)
        self.assertNotEqual(OtpModel.objects.get().otp_code, code)
        self.assertEqual(self.verify(challenge, code).status_code, 200)
        self.assertEqual(self.verify(challenge, code).status_code, 400)
        self.assertEqual(UserModel.objects.count(), 1)
        self.assertEqual(OtpModel.objects.get().otp_code, "")

    def test_invalid_expired_exhausted_and_stale_challenges(self):
        challenge, code = self.issue()
        stale = {**challenge, "challenge_id": str(uuid.uuid4())}
        self.assertEqual(self.verify(stale, code).status_code, 400)
        for _ in range(5):
            self.assertEqual(self.verify(challenge, "999999" if code != "999999" else "111111").status_code, 400)
        result = self.verify(challenge, code)
        self.assertIn("attempts_exhausted", result.data["error"]["fields"]["otp_code"])
        self.assertFalse(UserModel.objects.exists())

    def test_expiry_uses_fixed_deadline(self):
        challenge, code = self.issue()
        with patch("authentication.otp.now", return_value=self.clock + timedelta(minutes=5)):
            result = self.verify(challenge, code)
        self.assertIn("expired", result.data["error"]["fields"]["otp_code"])

    def test_resend_idempotency_cooldown_and_failure(self):
        request_id = str(uuid.uuid4())
        payload = {"phone_number": PHONE, "request_id": request_id}
        a = self.client.post("/api/auth/send-otp/", payload, format="json")
        b = self.client.post("/api/auth/send-otp/", payload, format="json")
        self.assertEqual(a.data["data"], b.data["data"])
        self.assertEqual(self.deliver.call_count, 1)
        c = self.client.post("/api/auth/send-otp/", {"phone_number": PHONE}, format="json")
        self.assertEqual(c.status_code, 429)
        self.assertEqual(int(c["Retry-After"]), 60)
        with patch("authentication.otp.now", return_value=self.clock + timedelta(seconds=61)):
            self.deliver.side_effect = RuntimeError("synthetic outage")
            self.assertEqual(self.client.post("/api/auth/send-otp/", {"phone_number": PHONE}, format="json").status_code, 503)
        self.assertEqual(OtpModel.objects.get().otp_code, "")

    @override_settings(OTP_GLOBAL_HOURLY_LIMIT=1)
    def test_global_spend_guard(self):
        self.issue()
        self.assertEqual(self.client.post("/api/auth/send-otp/", {"phone_number": "+15005550102"}, format="json").status_code, 429)
        self.assertEqual(self.deliver.call_count, 1)

    def test_interrupted_delivery_does_not_leave_retry_pending_forever(self):
        request_id = str(uuid.uuid4())
        payload = {"phone_number": PHONE, "request_id": request_id}
        self.assertEqual(self.client.post("/api/auth/send-otp/", payload, format="json").status_code, 200)
        OtpModel.objects.update(delivery_state="sending")  # Worker died before recording outcome.
        self.assertEqual(self.client.post("/api/auth/send-otp/", payload, format="json").status_code, 409)
        with patch("authentication.otp.now", return_value=self.clock + timedelta(minutes=5, seconds=1)):
            self.assertEqual(self.client.post("/api/auth/send-otp/", payload, format="json").status_code, 503)
            payload["request_id"] = str(uuid.uuid4())
            self.assertEqual(self.client.post("/api/auth/send-otp/", payload, format="json").status_code, 200)
        self.assertEqual(self.deliver.call_count, 2)

    def test_public_verification_ignores_expired_authorization(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired", HTTP_X_SRISU_CONTRACT="core-1")
        self.issue()

    def test_normalization_and_invalid_input(self):
        result = self.client.post("/api/auth/send-otp/", {"phone_number": "+1 (500) 555-0101"}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.deliver.call_args.args[0], PHONE)
        self.assertEqual(self.client.post("/api/auth/send-otp/", {"phone_number": "+notanumber"}, format="json").status_code, 400)

    def test_incomplete_capabilities_incremental_profile_skip_and_handoff(self):
        self.login()
        self.assertEqual(self.client.get("/api/chat/rooms/").status_code, 403)
        initial = self.client.get("/api/auth/setup-profile/").data["data"]["progress"]
        self.assertEqual(initial["next_step"], "name")
        named = self.client.patch("/api/auth/setup-profile/", {"full_name": "  सृजन Khadka  ", "username": "srijan"}, format="json")
        self.assertEqual(named.status_code, 200, named.data)
        from tools.check_core_contracts import validate_contract
        validate_contract("authProfile", named.data)
        self.assertEqual(named.data["data"]["progress"]["next_step"], "photo")
        done = self.client.patch("/api/auth/setup-profile/", {"skip_photo": True}, format="json")
        self.assertEqual(done.data["data"]["progress"]["next_step"], "complete")
        self.assertEqual(done.data["data"]["progress"]["membership"], "unlinked")
        self.assertEqual(self.client.get("/api/chat/rooms/").status_code, 200)
        from social.models import CoupleModel
        self.assertEqual(CoupleModel.objects.count(), 0)

    def test_profile_ownership_privileges_uniqueness_and_validation(self):
        self.login()
        other = UserModel.objects.create_user("+15005550102", username="taken")
        self.assertEqual(self.client.patch("/api/auth/setup-profile/", {"phone_number": other.phone_number, "full_name": "X"}, format="json").status_code, 403)
        bad = self.client.patch("/api/auth/setup-profile/", {"username": "taken"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("unique", bad.data["error"]["fields"]["username"])
        self.client.patch("/api/auth/setup-profile/", {"is_phone_verified": False, "is_staff": True, "is_engaged": True}, format="json")
        user = UserModel.objects.get(phone_number=PHONE)
        self.assertTrue(user.is_phone_verified)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_engaged)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserModel.objects.create_user("+15005550103", username="taken")

    def test_photo_validation_upload_and_retry(self):
        self.login()
        self.client.patch("/api/auth/setup-profile/", {"full_name": "Test User", "username": "test"}, format="json")
        invalid = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")
        self.assertEqual(self.client.patch("/api/auth/setup-profile/", {"profile_photo": invalid}, format="multipart").status_code, 400)
        image = BytesIO(); Image.new("RGB", (10, 10)).save(image, format="PNG")
        photo = SimpleUploadedFile("unsafe.png", image.getvalue(), content_type="text/plain")
        result = self.client.patch("/api/auth/setup-profile/", {"profile_photo": photo}, format="multipart")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["data"]["progress"]["profile_complete"])
        self.assertNotIn("unsafe", UserModel.objects.get(phone_number=PHONE).profile_photo.name)

    def test_nested_photo_cannot_attach_paths_or_mutate_another_owner(self):
        self.login()
        other = UserModel.objects.create_user("+15005550102")
        private = UserPhotoAlbumModel.objects.create(user=other, photo="synthetic/private.jpg")
        for value in ("synthetic/private.jpg", "https://example.test/private.jpg"):
            response = self.client.patch("/api/auth/setup-profile/", {"user_photos": [{"photo": value}]}, format="json")
            self.assertEqual(response.status_code, 400)
        response = self.client.patch("/api/auth/setup-profile/", {"user_photos": [{"id": private.pk, "removed": True}]}, format="json")
        self.assertEqual(response.status_code, 400)
        private.refresh_from_db()
        self.assertFalse(private.removed)
        self.assertEqual(UserPhotoAlbumModel.objects.count(), 1)

    @override_settings(AUTH_ACCEPT_LEGACY_TOKENS=False)
    def test_cutoff_rejects_legacy_client_before_consuming_proof(self):
        challenge, code = self.issue()
        self.client.credentials(HTTP_X_SRISU_CONTRACT="core-1")
        self.assertEqual(self.verify(challenge, code).status_code, 400)
        self.assertFalse(UserModel.objects.exists())
        self.client.credentials(HTTP_X_SRISU_CONTRACT="core-1", HTTP_X_SRISU_AUTH="auth-1")
        self.assertEqual(self.verify(challenge, code).status_code, 200)

    def test_refresh_rotation_replay_revocation_logout(self):
        tokens = self.login()
        access = AccessToken(tokens["access"])
        self.assertEqual(access["exp"] - access["iat"], 600)
        refreshed = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/setup-profile/").status_code, 401)
        self.assertIsNotNone(DeviceSession.objects.get().revoked_at)

    def test_logout_revokes_access_and_refresh_idempotently(self):
        tokens = self.login()
        for _ in range(2):
            self.assertEqual(self.client.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/setup-profile/").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code, 401)

    def test_legacy_completed_user_keeps_progress(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True, is_profile_complete=True)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get("/api/auth/setup-profile/").data["data"]["progress"]["next_step"], "complete")


class ConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_duplicate_send_reserves_one_delivery(self):
        from authentication.otp import DeliveryPending
        barrier, request_id = Barrier(2), uuid.uuid4()
        def run(_):
            try:
                barrier.wait(timeout=5)
                return request_code(PHONE, "synthetic-source", request_id)
            except DeliveryPending:
                return None
            finally:
                connections.close_all()
        with patch("authentication.otp.deliver") as sender:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, range(2)))
            accepted = next(result for result in results if result)
            self.assertEqual(request_code(PHONE, "synthetic-source", request_id)["challenge_id"], accepted["challenge_id"])
            self.assertEqual(sender.call_count, 1)
        self.assertEqual(OtpModel.objects.get().otp_attempts, 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_rotation_and_legacy_upgrade_are_idempotent(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True)
        for initial in (create_session(user)["refresh"], str(RefreshToken.for_user(user))):
            barrier, request_id = Barrier(2), uuid.uuid4()
            def run(_):
                try:
                    barrier.wait(timeout=5)
                    return rotate_refresh(initial, request_id)
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(pool.map(run, range(2)))
            self.assertEqual(first, second)
            sid = RefreshToken(first["refresh"])["sid"]
            self.assertIsNone(DeviceSession.objects.get(pk=sid).revoked_at)
        self.assertEqual(DeviceSession.objects.count(), 2)

    @skipUnlessDBFeature("has_select_for_update")
    def test_different_concurrent_refresh_proofs_revoke_session(self):
        from rest_framework.exceptions import AuthenticationFailed
        initial = create_session(UserModel.objects.create_user(PHONE))["refresh"]
        barrier = Barrier(2)
        def run(_):
            try:
                barrier.wait(timeout=5)
                rotate_refresh(initial, uuid.uuid4())
                return True
            except AuthenticationFailed:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run, range(2))), 1)
        self.assertIsNotNone(DeviceSession.objects.get().revoked_at)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_verification_consumes_once(self):
        with patch("authentication.otp.deliver") as send:
            challenge = request_code(PHONE, "test")
            code = send.call_args.args[1]
        def run(_):
            try:
                return verify_code(PHONE, code, uuid.UUID(challenge["challenge_id"])).pk
            except ValidationError:
                return None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(UserModel.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_username_claim_has_one_winner(self):
        def run(index):
            try:
                with transaction.atomic():
                    UserModel.objects.create_user(f"+1500555020{index}", username="shared")
                return True
            except IntegrityError:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sum(results), 1)


class SessionRecoveryTests(TransactionTestCase):
    # Channels closes old connections around database_sync_to_async. Use committed
    # transactions so the real PostgreSQL connection lifecycle is exercised.
    def test_lost_refresh_response_repeats_same_credentials_then_rejects_other_nonce(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True)
        initial = create_session(user)
        request_id = uuid.uuid4()
        at = now()
        with patch("authentication.sessions.now", return_value=at):
            first = rotate_refresh(initial["refresh"], request_id)
        with patch("authentication.sessions.now", return_value=at + timedelta(seconds=10)):
            second = rotate_refresh(initial["refresh"], request_id)
        self.assertEqual(first, second)
        from rest_framework.exceptions import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            rotate_refresh(initial["refresh"], uuid.uuid4())
        self.assertIsNotNone(DeviceSession.objects.get().revoked_at)

    def test_recovery_window_is_bounded(self):
        user = UserModel.objects.create_user(PHONE)
        initial = create_session(user)
        request_id = uuid.uuid4()
        at = now()
        with patch("authentication.sessions.now", return_value=at):
            rotate_refresh(initial["refresh"], request_id)
        from rest_framework.exceptions import AuthenticationFailed
        with patch("authentication.sessions.now", return_value=at + timedelta(seconds=61)), self.assertRaises(AuthenticationFailed):
            rotate_refresh(initial["refresh"], request_id)

    def test_legacy_upgrade_is_explicit_and_cutoff_is_configurable(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True, is_profile_complete=True)
        legacy = str(RefreshToken.for_user(user))
        migrated = rotate_refresh(legacy, uuid.uuid4())
        self.assertIn("sid", RefreshToken(migrated["refresh"]))
        from rest_framework.exceptions import AuthenticationFailed
        with override_settings(AUTH_ACCEPT_LEGACY_TOKENS=False), self.assertRaises(AuthenticationFailed):
            rotate_refresh(legacy)

    def test_incomplete_legacy_token_cannot_bypass_onboarding_capabilities(self):
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True)
        access = str(RefreshToken.for_user(user).access_token)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        self.assertEqual(client.get("/api/chat/rooms/").status_code, 403)
        self.assertEqual(client.get("/api/auth/setup-profile/").status_code, 200)
        from asgiref.sync import async_to_sync
        from chat.middleware import authenticate_token
        actor, expiry = async_to_sync(authenticate_token)(access)
        self.assertFalse(actor.is_authenticated)
        self.assertIsNone(expiry)

    def test_socket_watchdog_checks_revocation(self):
        from asgiref.sync import async_to_sync
        from chat.middleware import user_is_active
        user = UserModel.objects.create_user(PHONE, is_phone_verified=True, is_profile_complete=True)
        pair = create_session(user)
        sid = RefreshToken(pair["refresh"])["sid"]
        self.assertTrue(async_to_sync(user_is_active)(user.pk, sid))
        DeviceSession.objects.filter(pk=sid).update(revoked_at=now())
        self.assertFalse(async_to_sync(user_is_active)(user.pk, sid))
