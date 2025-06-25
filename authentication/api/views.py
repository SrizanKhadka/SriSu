from .serializers import *
from rest_framework import status
from rest_framework.response import Response
from authentication.models import *
from rest_framework.views import APIView
import random
from rest_framework import permissions
from django.conf import settings
from twilio.rest import Client
from rest_framework_simplejwt.tokens import RefreshToken
from authentication.api.serializers import UserModelSerializer
from django.utils.timezone import now
from datetime import timedelta
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ModelViewSet
from utils.choices import GenderChoices
from datetime import date
from collections import defaultdict
from rest_framework.pagination import PageNumberPagination
from django.db.models import OuterRef, Exists, Subquery
from rest_framework.decorators import action


class SendOTPAPIView(APIView):

    http_method_names = ["post"]

    def get_object_user(self, phone_number):
        try:
            return UserModel.objects.get(phone_number=phone_number)
        except UserModel.DoesNotExist:
            return None

    def generate_otp(self, phone_number):
        otp_code = random.randint(100000, 999999)

        OtpModel.objects.update_or_create(
            phone_number=phone_number,
            defaults={
                "otp_code": str(otp_code),
                "otp_status": OtpStatusChoices.NEW,
                "last_request_time": now(),
            },
        )

        return otp_code

    def update_opt_attempts(self, phone_number):
        otp_record = OtpModel.objects.filter(phone_number=phone_number).first()
        if otp_record:
            otp_record.otp_attempts += 1
            otp_record.save()

    def unverfiy_user(self, phone_number):
        user = self.get_object_user(phone_number)
        if user:
            user.is_phone_verified = False
            user.save()

    def send_otp_sms(self, phone_number, otp_code):

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        try:
            client.messages.create(
                body=f"Your SriSu Verification Code is {otp_code}",
                from_=settings.TWILIO_PHONE_NUMBER,
                to=phone_number,
            )
            self.update_opt_attempts(
                phone_number
            )  # Update OTP attempts after the otp is sent successfully.
            return True
        except Exception as e:
            print(f"OTP Send Error: {str(e)}")
            return False

    def can_request_otp(self, phone_number):

        otp_record = OtpModel.objects.filter(phone_number=phone_number).first()

        if otp_record:
            now_time = now()
            time_diff = now_time - otp_record.last_request_time

            # Reset count if 10 minutes have passed
            if time_diff > timedelta(minutes=10):
                otp_record.otp_attempts = 0
                otp_record.last_request_time = now_time
                otp_record.save()
                return True

            # If less than 10 mins & attempts exceed limit, block request
            if otp_record.otp_attempts >= 3:
                return False

            return True
        return True

    def post(self, request, *args, **kwargs):
        serializer = SendOtpSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        self.unverfiy_user(
            phone_number
        )  # Unverify user's phone number if exists while requesting for new otp.

        if not self.can_request_otp(phone_number):
            return Response(
                {"error": "Too many OTP requests. Try again in few minutes."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = self.generate_otp(phone_number)

        success = self.send_otp_sms(phone_number, otp_code)

        if success:
            return Response(
                {"message": "OTP sent successfully."}, status=status.HTTP_200_OK
            )
        return Response(
            {"error": "Failed to send OTP."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class VerifyOTPAPIView(APIView):
    http_method_names = ["post"]

    def generate_tokens(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    def post(self, request, *args, **kwargs):
        serializer = VerifyOtpSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        phone_number = request.data["phone_number"]

        # Update the otp model as otp expired as after the otp is used, it is expired.
        OtpModel.objects.filter(phone_number=phone_number).update(
            otp_status=OtpStatusChoices.EXPIRED
        )

        print("PHONE NUMBER = ", phone_number)
        user, created = UserModel.objects.update_or_create(
            phone_number=phone_number,  # Lookup_field
            defaults={"is_phone_verified": True},  # Fields to update
        )

        response_data = self.generate_tokens(user=user)

        user_data = UserModelSerializer(user).data

        return Response(
            {
                "message": "Phone number verified successfully.",
                "data": {
                    "user": user_data,
                    "tokens": response_data,
                },
            },
            status=status.HTTP_200_OK,
        )


class SetUpProfileAPIView(APIView):
    http_method_names = ["put", "patch"]  # Allow PUT and PATCH for updates
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, phone_number):
        return UserModel.objects.filter(phone_number=phone_number).first()

    def update(self, request, *args, **kwargs):
        phone_number = request.data.get("phone_number")

        if not phone_number:
            raise ValidationError({"error": "Phone number is required."})

        user = self.get_object(phone_number)
        if not user:
            raise ValidationError(
                {"error": "User with this phone number does not exist."}
            )
        elif not user.is_phone_verified:
            raise ValidationError({"error": "Phone number is not verified yet."})

        # Partial update to allow updating only provided fields
        # serializer = SetUpProfileSerializer(user, data=request.data, partial=True)
        serializer = SetUpProfileSerializer(user, data=request.data, partial=True, context={"request": request})

        serializer.is_valid(raise_exception=True)
        serializer.save(is_profile_complete=True)

        return Response(
            {
                "message": "Profile updated successfully",
                "data": {"user": serializer.data},
            },
            status=status.HTTP_200_OK,
        )

    def put(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)


class UserPreferenceView(ModelViewSet):
    queryset = UserPreferenceModel.objects.all()
    serializer_class = UserPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def validate_user_request(self, data, user):
        user_data = data.get("user")
        if not user_data or user_data.id != user.id:
            raise ValidationError({"error": "User does not match."})

        if not user.is_profile_complete:
            raise ValidationError({"error": "User profile is not complete."})

        if not user.is_phone_verified:
            raise ValidationError({"error": "User phone number is not verified."})
    
    def get_object(self):
        obj = UserPreferenceModel.objects.filter(user=self.request.user).first()
        if not obj:
           return None
        return obj
        
    @action(detail=False, methods=["get"], url_path="me")
    def get_my_preference(self, request):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.validate_user_request(data=serializer.validated_data, user=request.user)
        self.perform_create(serializer)

        return Response(
            {
                "message": "User preference created successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        self.validate_user_request(data=serializer.validated_data, user=request.user)

        self.perform_update(serializer)

        return Response(
            {
                "message": "User preference updated successfully.",
                "data": serializer.data,
            }
        )

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()
        
class UserSuggestionPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100
       
class UserSuggestionView(ModelViewSet):
    queryset = UserModel.objects.all()
    serializer_class = UserSuggestionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UserSuggestionPagination
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        user = self.request.user
        preferences = UserPreferenceModel.objects.filter(user=user).first()
        gender = GenderChoices.MALE if user.gender == GenderChoices.FEMALE else GenderChoices.FEMALE
        
        connection_sent = SingleConnectionModel.objects.filter(
            sender_number=user.phone_number,
            receiver_number=OuterRef('phone_number'),
        )
        
        if not preferences:
            all_users = UserModel.objects.exclude(id=user.id).filter(gender=gender)
            all_users = list(all_users.annotate(
                crushed=Exists(connection_sent)
            ))
            
            random.seed(request.user.id)
            random.shuffle(all_users)

            page = self.paginate_queryset(all_users)
            serializer = self.get_serializer(page, many=True)
            return Response({
                "data": {
                    "count": self.paginator.page.paginator.count,
                    "next": self.paginator.get_next_link(),
                    "previous": self.paginator.get_previous_link(),
                    "results": serializer.data
                },
                "message": "User Suggestions fetched successfully."
            })



        today = date.today()
        min_birth_year = today.year - preferences.max_age
        max_birth_year = today.year - preferences.min_age
        zodiac_sign = preferences.zodiac_sign

        filtered_users = UserModel.objects.exclude(id=user.id)
        
        if user.city:
            filtered_users = filtered_users.filter(city=user.city)

        if gender:
            filtered_users = filtered_users.filter(gender=gender)

        if zodiac_sign:
            filtered_users = filtered_users.filter(zodiac_sign=zodiac_sign)

        if min_birth_year and max_birth_year:
            filtered_users = filtered_users.filter(dob__year__range=(min_birth_year, max_birth_year))
            
        
        filtered_users = filtered_users.annotate(
            crushed=Exists(connection_sent)
        )

        all_interest_qs = UserInterestModel.objects.filter(user__in=filtered_users)
        
        #Build a dictionary {user_id: set of interests}
        user_interest_map = defaultdict(set)
        for obj in all_interest_qs:
            user_interest_map[obj.user_id].add(obj.name)

        user_interests = set(
            UserInterestModel.objects.filter(user=user).values_list("name", flat=True)
        )

        scored_users = []
        for candidate in filtered_users:
            candidate_interests = user_interest_map.get(candidate.id, set())
            intersection = user_interests & candidate_interests

            score = int((len(intersection) / len(user_interests)) * 100) if user_interests else 0
            print('SCORE = ', score)
            scored_users.append((candidate, score))

        strong = [user for user, score in scored_users if score >= 70]
        medium = [user for user, score in scored_users if 40 <= score < 70]
        weak = [user for user, score in scored_users if score < 40]
        
        sorted_users = strong + medium + weak

        page = self.paginate_queryset(sorted_users)
        serializer = self.get_serializer(page, many=True)
        return Response({
            "data": {
                "count": self.paginator.page.paginator.count,
                "next": self.paginator.get_next_link(),
                "previous": self.paginator.get_previous_link(),
                "results": serializer.data
            },
            "message": "User Suggestions fetched successfully."
        })






