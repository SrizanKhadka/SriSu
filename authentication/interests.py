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
    catalogue = {item.pk: item for item in InterestModel.objects.order_by("id")}
    by_name = {}
    for item in catalogue.values():
        if display_label(item.name):
            by_name.setdefault(display_label(item.name).casefold(), item)
    rows = list(UserInterestModel.objects.filter(user=user).order_by("id"))
    owned = {row.pk: row for row in rows}
    plans = []
    for entry in values:
        saved = owned.get(entry.get("id"))
        if entry.get("id") and saved is None:
            raise serializers.ValidationError({"user_interests": "This selection is no longer available."})
        if saved and entry.get("interest") and saved.interest_id not in (None, entry["interest"]):
            raise serializers.ValidationError({"user_interests": "The selection does not match its interest."})
        interest_id = entry.get("interest") or (saved.interest_id if saved else None)
        name = display_label(saved.name if saved else entry.get("name"))
        if interest_id:
            interest = catalogue.get(interest_id)
            if interest is None:
                raise serializers.ValidationError({"user_interests": "An interest is no longer available. Refresh the list."})
            name = display_label(interest.name)
        else:
            # Older clients identify selections only by their displayed name.
            # Preserve links even when an old row label differs from the catalogue.
            linked = next((row for row in rows if row.interest_id and display_label(row.name).casefold() == name.casefold()), None)
            item = by_name.get(name.casefold())
            interest_id = linked.interest_id if linked else (item.pk if item else None)
            if item:
                name = display_label(item.name)
        if not name and not (saved and entry["removed"]):
            raise serializers.ValidationError({"user_interests": "Choose a named interest."})
        if interest_id and not display_label(catalogue[interest_id].name) and not entry["removed"]:
            raise serializers.ValidationError({"user_interests": "Choose a named interest."})
        plans.append((entry, saved, interest_id, name))
    for entry, saved, interest_id, name in plans:
        keys = {name.casefold()}
        if saved:
            keys.add(display_label(saved.name).casefold())
        matches = [row for row in rows if row == saved or
                   (interest_id is not None and row.interest_id == interest_id) or
                   display_label(row.name).casefold() in keys]
        existing = saved or next(iter(matches), None)
        if existing:
            duplicates = [row for row in matches if row.pk != existing.pk]
            UserInterestModel.objects.filter(pk__in=[row.pk for row in duplicates]).update(removed=True)
            for row in duplicates:
                row.removed = True
            existing.name, existing.interest_id, existing.removed = name, interest_id, entry["removed"]
            existing.save(update_fields=["name", "interest", "removed"])
        elif not entry["removed"]:
            rows.append(UserInterestModel.objects.create(user=user, interest_id=interest_id, name=name, removed=False))
