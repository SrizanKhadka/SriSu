"""Aggregate operational counters only: never expose installation or recipient rows."""
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from django.utils import timezone
from notifications.models import Delivery, Installation


class Command(BaseCommand):
    help = "Show content-free notification queue and outcome counters."

    def handle(self, *args, **options):
        oldest = Delivery.objects.filter(state__in=["pending", "sending"]).aggregate(value=Min("event__created_at"))["value"]
        self.stdout.write(json.dumps({
            "enabled": settings.NOTIFICATIONS_ENABLED,
            "active_installations": Installation.objects.filter(active=True).count(),
            "oldest_pending_seconds": max(0, int((timezone.now()-oldest).total_seconds())) if oldest else 0,
            "deliveries": list(Delivery.objects.values("state", "outcome").annotate(count=Count("pk")).order_by("state", "outcome")),
        }))
