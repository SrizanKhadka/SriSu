import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count, F, OuterRef, Subquery
from django.db.models.functions import Coalesce


def require_linked_legacy_memberships(apps, schema_editor):
    """Do not guess whether an unlinked historical couple is still active."""
    Membership = apps.get_model("social", "CoupleMembershipModel")
    orphan_couples = list(
        Membership.objects.filter(couple__couple_connection__isnull=True)
        .values_list("couple_id", flat=True)
        .distinct()
        .order_by("couple_id")[:50]
    )
    if orphan_couples:
        detail = ", ".join(str(couple_id) for couple_id in orphan_couples)
        raise RuntimeError(
            "social 0014 found memberships whose couple has no relationship "
            "identity (first 50 couple ids: "
            f"{detail}). Explicitly link or retire those legacy couples before retrying."
        )


def backfill_relationship_times(apps, schema_editor):
    Connection = apps.get_model("social", "CoupleConnectionModel")
    Couple = apps.get_model("social", "CoupleModel")
    Membership = apps.get_model("social", "CoupleMembershipModel")
    Connection.objects.filter(connection_status="ACCEPTED", accepted_at__isnull=True).update(
        accepted_at=F("updated_at")
    )
    Connection.objects.filter(connection_status="BREAK-UP", ended_at__isnull=True).update(
        ended_at=F("updated_at")
    )
    relationship_end = (
        Couple.objects.filter(pk=OuterRef("couple_id"))
        .annotate(
            lifecycle_end=Coalesce(
                "couple_connection__ended_at",
                "couple_connection__updated_at",
            )
        )
        .values("lifecycle_end")[:1]
    )
    Membership.objects.filter(ended_at__isnull=True).exclude(
        couple__couple_connection__connection_status="ACCEPTED"
    ).update(ended_at=Subquery(relationship_end))


def require_unique_active_memberships(apps, schema_editor):
    """Fail with actionable evidence instead of an opaque unique-index error."""
    Membership = apps.get_model("social", "CoupleMembershipModel")
    conflicts = list(
        Membership.objects.filter(ended_at__isnull=True)
        .values("user_id")
        .annotate(active_count=Count("id"))
        .filter(active_count__gt=1)
        .order_by("user_id")[:50]
    )
    if not conflicts:
        return

    evidence = []
    for conflict in conflicts:
        couple_ids = list(
            Membership.objects.filter(
                user_id=conflict["user_id"],
                ended_at__isnull=True,
            )
            .order_by("couple_id")
            .values_list("couple_id", flat=True)[:20]
        )
        evidence.append(
            f"{conflict['user_id']}:{','.join(str(value) for value in couple_ids)}"
        )
    raise RuntimeError(
        "social 0014 found users with multiple active relationship memberships "
        "after lifecycle backfill (user_id:couple_ids, first 50 users): "
        + "; ".join(evidence)
        + ". Explicitly reconcile which relationship remains active before retrying."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0013_cover_bounds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            require_linked_legacy_memberships,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="coupleconnectionmodel",
            name="acceptance_operation_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coupleconnectionmodel",
            name="accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coupleconnectionmodel",
            name="ended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coupleconnectionmodel",
            name="request_operation_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coupleconnectionmodel",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="couplemembershipmodel",
            name="ended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="couplemembershipmodel",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="couple_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_relationship_times, migrations.RunPython.noop),
        migrations.RunPython(
            require_unique_active_memberships,
            migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name="couplemembershipmodel",
            index=models.Index(fields=["user", "ended_at"], name="couple_member_active_idx"),
        ),
        migrations.AddConstraint(
            model_name="coupleconnectionmodel",
            constraint=models.UniqueConstraint(
                condition=models.Q(request_operation_id__isnull=False),
                fields=("sender_number", "request_operation_id"),
                name="unique_couple_request_operation",
            ),
        ),
        migrations.AddConstraint(
            model_name="couplemembershipmodel",
            constraint=models.UniqueConstraint(
                condition=models.Q(ended_at__isnull=True),
                fields=("user",),
                name="unique_active_couple_membership",
            ),
        ),
    ]
