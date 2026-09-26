"""Pure, explainable metadata scoring; coefficients are launch assumptions."""
import hashlib
import unicodedata


DEFAULT_WEIGHTS = {"interests": 40, "location": 25, "fave": 20, "freshness": 15}


def normalize(value):
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def interest_set(values):
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {normalize(value) for value in values if isinstance(value, str) and normalize(value)}


def rank_score(source_interests, candidate_interests, source_location, locations, is_faved,
               latest_at, as_of, weights=None):
    weights = DEFAULT_WEIGHTS if weights is None else weights
    union = source_interests | candidate_interests
    similarity = len(source_interests & candidate_interests) / len(union) if union else 0
    city, country = map(normalize, source_location)
    location = 0
    for other_city, other_country in locations:
        other_city, other_country = normalize(other_city), normalize(other_country)
        if country and country == other_country:
            location = max(location, 1 if city and city == other_city else 0.4)
    age = max(0, (as_of - latest_at).total_seconds() / 3600)
    return (weights["interests"] * similarity + weights["location"] * location
            + weights["fave"] * int(is_faved) + weights["freshness"] * 2 ** (-age / 8))


def compose(ranked_ids, faved_ids, seed, interval=10):
    """One card per couple; reserve every tenth position for stable exploration."""
    remaining = set(ranked_ids)
    exploration = sorted(remaining, key=lambda pk: (
        pk in faved_ids, hashlib.sha256(f"{seed}:{pk}".encode()).digest(), pk))
    ranked = iter(ranked_ids)
    shuffled = iter(exploration)
    result = []
    while remaining:
        source = shuffled if interval > 0 and (len(result) + 1) % interval == 0 else ranked
        item = next(pk for pk in source if pk in remaining)
        result.append(item)
        remaining.remove(item)
    return result
