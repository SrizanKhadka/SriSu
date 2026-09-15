import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_existing_couple_members(apps, schema_editor):
    Couple = apps.get_model("social", "CoupleModel")
    Membership = apps.get_model("social", "CoupleMembershipModel")

    for couple in Couple.objects.all().iterator():
        member_data = [
            (couple.male_partner_id, 1, couple.nickname_for_male),
            (couple.female_partner_id, 2, couple.nickname_for_female),
        ]
        for user_id, position, nickname in member_data:
            conflict = Membership.objects.filter(user_id=user_id).exclude(
                couple_id=couple.id
            )
            if conflict.exists():
                raise RuntimeError(
                    f"User {user_id} belongs to more than one existing couple. "
                    "Resolve the conflict before applying this migration."
                )
            Membership.objects.update_or_create(
                user_id=user_id,
                defaults={
                    "couple_id": couple.id,
                    "position": position,
                    "nickname": nickname,
                    "is_owner": True,
                },
            )

        has_profile_data = any(
            [
                couple.anniversary_date,
                couple.shared_dreams,
                couple.shared_interests,
                couple.relationship_tagline,
                couple.cover_photo,
            ]
        )
        if has_profile_data:
            couple.profile_completed_at = couple.updated_at
            couple.save(update_fields=["profile_completed_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("social", "0006_couplemomentmodel_couplemomentphotomodel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name="couplemodel",
            old_name="couple_profile_photo",
            new_name="cover_photo",
        ),
        migrations.AddField(
            model_name="couplemodel",
            name="journey_story",
            field=models.TextField(blank=True, max_length=3000, null=True),
        ),
        migrations.AddField(
            model_name="couplemodel",
            name="profile_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="couplemodel",
            name="relationship_strength",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="couplemodel",
            name="title",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.CreateModel(
            name="CoupleMembershipModel",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "position",
                    models.PositiveSmallIntegerField(
                        choices=[(1, "Partner one"), (2, "Partner two")]
                    ),
                ),
                ("nickname", models.CharField(blank=True, max_length=30, null=True)),
                ("is_owner", models.BooleanField(default=True)),
                ("joined_at", models.DateTimeField(auto_now_add=True)),
                (
                    "couple",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="social.couplemodel",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="couple_membership",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["position"],
                "indexes": [
                    models.Index(
                        fields=["couple", "position"],
                        name="social_coup_couple__d50799_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("couple", "position"),
                        name="unique_couple_member_position",
                    ),
                    models.UniqueConstraint(
                        fields=("couple", "user"),
                        name="unique_user_per_couple",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="couplemodel",
            name="members",
            field=models.ManyToManyField(
                related_name="couple_profiles",
                through="social.CoupleMembershipModel",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            migrate_existing_couple_members,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="couplemodel",
            name="male_partner",
        ),
        migrations.RemoveField(
            model_name="couplemodel",
            name="female_partner",
        ),
        migrations.RemoveField(
            model_name="couplemodel",
            name="nickname_for_male",
        ),
        migrations.RemoveField(
            model_name="couplemodel",
            name="nickname_for_female",
        ),
    ]
