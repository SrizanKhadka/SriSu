from django.db.models import Exists, OuterRef, Q, Prefetch

from authentication.models import (
    UserModel,
    UserInterestModel,
)
from social.models import UserPreferenceModel, SingleConnectionModel
from utils.choices import GenderChoices, SingleConnectionStatus

# This layer is responsible only for fetching data cleanly.
class SuggestionRepository:
    def __init__(self, user):
        self.user = user

    def get_user_preferences(self):
        return UserPreferenceModel.objects.filter(user=self.user).first()

    def get_user_interests(self):
        return set(
            UserInterestModel.objects.filter(user=self.user).values_list("name", flat=True)
        )

    def get_target_gender(self):
        if self.user.gender == GenderChoices.FEMALE:
            return GenderChoices.MALE
        if self.user.gender == GenderChoices.MALE:
            return GenderChoices.FEMALE
        return None

    def get_active_connection_subquery(self):
        return SingleConnectionModel.objects.filter(
            Q(
                sender_number=self.user.phone_number,
                receiver_number=OuterRef("phone_number"),
            )
            | Q(
                receiver_number=self.user.phone_number,
                sender_number=OuterRef("phone_number"),
            )
        ).exclude(
            connection_status__in=[
                SingleConnectionStatus.NOTHING,
                SingleConnectionStatus.REJECTED,
            ]
        )

    def get_base_candidate_queryset(self):
        """
        Base queryset for possible candidates.
        Keep this queryset lean and optimized for suggestion listing.
        """
        target_gender = self.get_target_gender()
        connection_subquery = self.get_active_connection_subquery()

        queryset = (
            UserModel.objects.exclude(id=self.user.id)
            .annotate(has_active_connection=Exists(connection_subquery))
            .prefetch_related(
                Prefetch(
                    "user_interests",
                    queryset=UserInterestModel.objects.only("id", "user_id", "name"),
                    to_attr="prefetched_interests",
                )
            )
        )

        if target_gender:
            queryset = queryset.filter(gender=target_gender)

        return queryset

    def apply_preference_filters(self, queryset, preferences, dob_start=None, dob_end=None):
        if not preferences:
            return queryset

        if preferences.city:
            queryset = queryset.filter(city=preferences.city)
        elif preferences.country:
            queryset = queryset.filter(country=preferences.country)

        if preferences.zodiac_sign:
            queryset = queryset.filter(zodiac_sign=preferences.zodiac_sign)

        if dob_start and dob_end:
            queryset = queryset.filter(dob__range=(dob_start, dob_end))

        return queryset