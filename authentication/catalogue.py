"""Public interest catalogue only; user selections are never stored in this cache."""
from django.core.cache import caches
from django.db.models.signals import post_delete, post_save

from authentication.models import InterestCategory, InterestModel
from srisu.api.cache import PerformanceCache


def catalogue_cache():
    return PerformanceCache(caches["default"], "interest-catalogue", ttl=300)


def interest_catalogue():
    from authentication.api.serializers import InterestSerializer
    return catalogue_cache().read(
        scope="public-catalogue", resource="all",
        loader=lambda: list(InterestSerializer(
            InterestModel.objects.select_related("category").order_by("name", "id"), many=True,
        ).data),
    )


def invalidate_catalogue(sender, **kwargs):
    catalogue_cache().invalidate_on_commit(scope="public-catalogue")


def register_catalogue_signals():
    for model in (InterestModel, InterestCategory):
        for signal in (post_save, post_delete):
            signal.connect(invalidate_catalogue, sender=model, weak=False,
                           dispatch_uid=f"catalogue:{model.__name__}:{id(signal)}")
