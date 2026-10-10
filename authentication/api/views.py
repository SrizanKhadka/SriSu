from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied
from authentication.progress import profile_progress

from django.conf import settings
from django.utils.timezone import now
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.api.serializers import (
    InterestSerializer,
    SendOtpSerializer,
    SetUpProfileSerializer,
    UserModelSerializer,
    VerifyOtpSerializer,
)
from authentication.models import UserInterestModel, UserModel, UserPhotoAlbumModel


class SendOTPAPIView(APIView):
    http_method_names = ["post"]
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        from authentication.otp import request_code
        serializer = SendOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = request_code(data["phone_number"], request.META.get("REMOTE_ADDR", "unknown"), data.get("request_id"))
        message = "Mock OTP request accepted. No SMS was sent." if settings.OTP_MOCK_DELIVERY else "OTP sent successfully."
        return Response({"message": message, "data": result}, headers={"Cache-Control": "no-store"})


class VerifyOTPAPIView(APIView):
    http_method_names = ["post"]
    authentication_classes = []
    permission_classes = [AllowAny]

    @staticmethod
    def generate_tokens(user):
        refresh = RefreshToken.for_user(user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}

    def session_tokens(self, user, request):
        if request.headers.get("X-SriSu-Auth") == "auth-1":
            from authentication.sessions import create_session
            return create_session(user)
        return self.generate_tokens(user)

    def post(self, request):
        from authentication.otp import verify_code
        if not settings.AUTH_ACCEPT_LEGACY_TOKENS and request.headers.get("X-SriSu-Auth") != "auth-1":
            raise ValidationError("Update SriSu to sign in.", code="client_update_required")
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = verify_code(data["phone_number"], data["otp_code"], data.get("challenge_id"))
        return Response({"message": "Phone number verified successfully.", "data": {
            "user": UserModelSerializer(user, context={"request": request}).data,
            "tokens": self.session_tokens(user, request),
            "progress": profile_progress(user),
        }}, headers={"Cache-Control": "no-store"})


class SetUpProfileAPIView(APIView):
    http_method_names = ["get", "put", "patch"]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        if not user or not user.is_authenticated:
            raise ValidationError({"error": "User does not exist."})

        serializer = SetUpProfileSerializer(user, context={"request": request})
        return Response(
            {
                "message": "User profile retrieved successfully",
                "data": {"user": serializer.data, "progress": profile_progress(user)},
            },
            status=status.HTTP_200_OK,
            headers={"Cache-Control": "private, no-store"},
        )

    def put(self, request, *args, **kwargs):
        return self.update(request)

    def patch(self, request, *args, **kwargs):
        return self.update(request)

    def update(self, request):
        if not request.user or not request.user.is_authenticated:
            raise ValidationError({"error": "Authentication required."})

        payload = request.data.copy()
        if payload.get("phone_number") not in (None, "", request.user.phone_number):
            raise PermissionDenied("You cannot update another profile.")
        if not request.user.is_phone_verified:
            raise PermissionDenied("Verify your phone number first.")
        try:
            with transaction.atomic():
                user = UserModel.objects.select_for_update().get(pk=request.user.pk)
                user_interests_data = payload.pop("user_interests", [])
                user_photos_data = payload.pop("user_photos", [])
                serializer = SetUpProfileSerializer(user, data=payload, partial=True, context={"request": request})
                serializer.is_valid(raise_exception=True)
                # Validate before any related mutation. The transaction rolls back failures.
                self.manage_user_interests(user, user_interests_data)
                self.manage_user_photos(user, user_photos_data)
                serializer.save()
                # Old clients submit all historical required fields in one operation; a
                # missing optional photo in that payload is their established skip action.
                if request.headers.get("X-SriSu-Auth") != "auth-1" and all(
                    payload.get(field) for field in ("full_name", "username", "gender", "zodiac_sign", "dob")
                ):
                    user.profile_photo_skipped = not bool(user.profile_photo)
                    user.is_profile_complete = True
                    user.save(update_fields=["profile_photo_skipped", "is_profile_complete"])
        except IntegrityError:
            raise ValidationError({"username": "This username is taken."}, code="unique") from None
        return Response({"message": "Profile updated successfully", "data": {
            "user": SetUpProfileSerializer(user, context={"request": request}).data,
            "progress": profile_progress(user),
        }}, headers={"Cache-Control": "private, no-store"})

    @staticmethod
    def manage_user_photos(user, user_photos_data):
        for photo_data in user_photos_data:
            photo_id = photo_data.get("id")
            removed = photo_data.get("removed", False)
            new_photo = photo_data.get("photo")
            if new_photo:
                # Legacy nested uploads share the same decode/size/filename boundary.
                # A string path or URL must never attach existing private media.
                from rest_framework import serializers
                field = serializers.ImageField()
                new_photo = SetUpProfileSerializer().validate_profile_photo(field.run_validation(new_photo))

            if not photo_id:
                # Case 1: New photo upload
                if new_photo:
                    if UserPhotoAlbumModel.objects.filter(user=user, removed=False).count() >= 10:
                        raise ValidationError({"user_photos": "You can only upload 10 photos."})
                    UserPhotoAlbumModel.objects.create(
                        user=user,
                        photo=new_photo,
                        removed=False,
                    )
                continue

            try:
                photo_instance = UserPhotoAlbumModel.objects.get(id=photo_id, user=user)
            except UserPhotoAlbumModel.DoesNotExist:
                raise ValidationError({"error": f"Photo with id {photo_id} not found."})

            if removed:
                #Case 2: Mark as removed
                photo_instance.removed = True
                photo_instance.save(update_fields=["removed", "updated_date"])
                continue

            if new_photo:
                #Case 3:: Updating the photo
                #Store the old photo in the album befoore reloacing
                UserPhotoAlbumModel.objects.create(
                    user=user,
                    photo=photo_instance.photo,
                    removed=True,
                )
                #Update the instance with the new photo
                photo_instance.photo = new_photo
                photo_instance.created_date = now()
                photo_instance.save(update_fields=["photo", "created_date", "updated_date"])

    @staticmethod
    def manage_user_interests(user, user_interests_data):
        for interest_data in user_interests_data:
            name = interest_data.get("name")
            interest_id = interest_data.get("interest")
            removed = interest_data.get("removed", False)

            if not name:
                continue

            interest_qs = UserInterestModel.objects.filter(user=user, name=name)

            if not interest_qs.exists():
                UserInterestModel.objects.create(
                    user=user,
                    name=name,
                    interest_id=interest_id,
                    removed=removed,
                )
                continue

            if removed:
                interest_qs.update(removed=True)
            elif interest_qs.filter(removed=True).exists():
                interest_qs.update(removed=False, interest_id=interest_id)


class InterestsAPIView(APIView):
    http_method_names = ["get"]
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from authentication.catalogue import interest_catalogue
        return Response(
            {
                "message": "User interests retrieved successfully.",
                "data": {"interests": interest_catalogue()},
            },
            status=status.HTTP_200_OK,
        )


class RefreshSessionAPIView(APIView):
    def get_authenticate_header(self, request):
        return "Bearer"

    authentication_classes = []
    permission_classes = [AllowAny]
    http_method_names = ["post"]

    def post(self, request):
        from rest_framework import serializers
        from authentication.sessions import rotate_refresh
        field = serializers.CharField(max_length=4096)
        value = field.run_validation(request.data.get("refresh"))
        return Response({"data": {"tokens": rotate_refresh(value, serializers.UUIDField(required=False).run_validation(request.data["request_id"]) if "request_id" in request.data else None)}}, headers={"Cache-Control": "no-store"})


class LogoutSessionAPIView(APIView):
    # Refresh proof permits revocation even if access expired; local logout needn't wait.
    authentication_classes = []
    permission_classes = [AllowAny]
    http_method_names = ["post"]

    def post(self, request):
        from authentication.models import DeviceSession
        from rest_framework import serializers
        from rest_framework_simplejwt.exceptions import TokenError
        value = serializers.CharField(max_length=4096).run_validation(request.data.get("refresh"))
        try:
            token = RefreshToken(value)
            if token.get("sid"):
                DeviceSession.objects.filter(pk=token["sid"], user_id=token["user_id"], revoked_at__isnull=True).update(revoked_at=now())
        except TokenError:
            pass  # Idempotent revocation: an invalid/expired proof has no active authority.
        return Response(status=204, headers={"Cache-Control": "no-store"})
