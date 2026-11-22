"""Stable catalogue presentation and actor-scoped interest writes."""
from rest_framework import serializers

from authentication.models import InterestModel, UserInterestModel


def display_label(value):
    return " ".join((value or "").split())


def catalogue_rows():
    from authentication.api.serializers import InterestSerializer
    rows = list(InterestSerializer(InterestModel.objects.select_related("category").order_by("id"), many=True).data)
    result = []
    for row in rows:
        row["name"] = display_label(row["name"])
        if not row["name"]:
            continue
        category = row.get("category")
        if category:
            category["label"] = display_label(category.get("label")) or display_label(category.get("name")).replace("_", " ").title() or "Other interests"
        result.append(row)
    # Keep every identifier, including legacy duplicates, so existing selections
    # remain valid. The client can present aliases together without deleting data.
    return sorted(result, key=lambda row: (
        (row.get("category") or {}).get("label", "Other interests").casefold(),
        row["name"].casefold(), row["id"],
    ))


class InterestSelectionSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    interest = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    removed = serializers.BooleanField(default=False)


def update_user_interests(user, entries):
    """Caller holds the user row lock in the existing profile transaction."""
    if not isinstance(entries, list) or len(entries) > 256:
        raise serializers.ValidationError({"user_interests": "Provide up to 256 interest selections."})
    serializer = InterestSelectionSerializer(data=entries, many=True)
    serializer.is_valid(raise_exception=False)
    if serializer.errors:
        raise serializers.ValidationError({"user_interests": serializer.errors})
    values = serializer.validated_data
    catalogue = InterestModel.objects.in_bulk({entry["interest"] for entry in values if entry.get("interest")})
    owned = UserInterestModel.objects.filter(user=user).in_bulk({entry["id"] for entry in values if entry.get("id")})
    for entry in values:
        if entry.get("id") and entry["id"] not in owned:
            raise serializers.ValidationError({"user_interests": "This selection is no longer available."})
        if entry.get("id") and entry.get("interest") and owned[entry["id"]].interest_id not in (None, entry["interest"]):
            raise serializers.ValidationError({"user_interests": "The selection does not match its interest."})
        if entry.get("interest") and entry["interest"] not in catalogue:
            raise serializers.ValidationError({"user_interests": "An interest is no longer available. Refresh the list."})
        if not entry.get("interest") and not entry.get("id") and not display_label(entry.get("name")):
            raise serializers.ValidationError({"user_interests": "Choose a named interest."})
    for entry in values:
        saved = owned.get(entry.get("id"))
        interest_id = entry.get("interest") or (saved.interest_id if saved else None)
        if interest_id:
            interest = catalogue.get(interest_id) or InterestModel.objects.get(pk=interest_id)
            name = display_label(interest.name)
            rows = UserInterestModel.objects.filter(user=user, interest_id=interest_id).order_by("id")
        else:
            name = display_label(saved.name if saved else entry.get("name"))
            rows = UserInterestModel.objects.filter(user=user, interest__isnull=True, name__iexact=name).order_by("id")
        existing = rows.first()
        if existing:
            rows.exclude(pk=existing.pk).update(removed=True)
            existing.name, existing.removed = name, entry["removed"]
            existing.save(update_fields=["name", "removed"])
        elif not entry["removed"]:
            UserInterestModel.objects.create(user=user, interest_id=interest_id, name=name, removed=False)
