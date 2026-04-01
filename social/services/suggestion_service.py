from dataclasses import dataclass
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from social.constants.suggestion_constants import DEFAULT_CANDIDATE_LIMIT
from social.repository.suggestion_repository import SuggestionRepository
from social.services.suggestion_scorer import SuggestionScorer


@dataclass
class RankedSuggestion:
    user: object
    score: int
    bucket: str


class UserSuggestionService:
    def __init__(self, user):
        self.user = user
        self.repository = SuggestionRepository(user=user)
        self.preferences = self.repository.get_user_preferences()
        self.user_interests = self.repository.get_user_interests()
        self.scorer = SuggestionScorer(
            source_user=user,
            source_user_interests=self.user_interests,
        )

    def get_ranked_users(self):
        queryset = self.repository.get_base_candidate_queryset()

        dob_start, dob_end = self._get_dob_range_from_preferences()
        queryset = self.repository.apply_preference_filters(
            queryset=queryset,
            preferences=self.preferences,
            dob_start=dob_start,
            dob_end=dob_end,
        )

        candidates = list(queryset[:DEFAULT_CANDIDATE_LIMIT])

        ranked = []
        for candidate in candidates:
            score = self.scorer.score(candidate)
            ranked.append(
                RankedSuggestion(
                    user=candidate,
                    score=score,
                    bucket=self.scorer.bucket(score),
                )
            )

        ranked.sort(
            key=lambda item: (
                item.score,
                getattr(item.user, "created_at", None) or timezone.now(),
            ),
            reverse=True,
        )

        return ranked

    def get_ranked_user_list(self):
        return [item.user for item in self.get_ranked_users()]

    def _get_dob_range_from_preferences(self):
        if not self.preferences:
            return None, None

        if not self.preferences.min_age or not self.preferences.max_age:
            return None, None

        today = timezone.localdate()
        latest_dob = today - relativedelta(years=self.preferences.min_age)
        earliest_dob = (
            today - relativedelta(years=self.preferences.max_age + 1)
        ) + timedelta(days=1)

        return earliest_dob, latest_dob