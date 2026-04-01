from social.constants.suggestion_constants import (
    SuggestionScore,
    SuggestionBucket,
    HIGH_SCORE_THRESHOLD,
    MEDIUM_SCORE_THRESHOLD,
    
)

class SuggestionScorer:
    def __init__(self, source_user, source_user_interests: set[str]):
        self.source_user = source_user
        self.source_user_interests = source_user_interests or set()

    def score(self, candidate):
        candidate_interests = {
            item.name for item in getattr(candidate, "prefetched_interests", [])
        }

        score = 0

        # Interest similarity using Jaccard similarity
        if self.source_user_interests or candidate_interests:
            union = self.source_user_interests | candidate_interests
            intersection = self.source_user_interests & candidate_interests
            similarity = len(intersection) / len(union) if union else 0
            score += round(similarity * SuggestionScore.INTEREST_SIMILARITY)

        if self.source_user.city and candidate.city and self.source_user.city == candidate.city:
            score += SuggestionScore.SAME_CITY

        if (
            self.source_user.country
            and candidate.country
            and self.source_user.country == candidate.country
        ):
            score += SuggestionScore.SAME_COUNTRY

        if (
            self.source_user.zodiac_sign
            and candidate.zodiac_sign
            and self.source_user.zodiac_sign == candidate.zodiac_sign
        ):
            score += SuggestionScore.SAME_ZODIAC

        if not getattr(candidate, "has_active_connection", False):
            score += SuggestionScore.NO_ACTIVE_CONNECTION

        return score

    @staticmethod
    def bucket(score: int) -> str:
        if score >= HIGH_SCORE_THRESHOLD:
            return SuggestionBucket.HIGH
        if score >= MEDIUM_SCORE_THRESHOLD:
            return SuggestionBucket.MEDIUM
        return SuggestionBucket.LOW