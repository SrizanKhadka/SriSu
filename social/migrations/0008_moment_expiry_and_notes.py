from datetime import timedelta
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_moments(apps, schema_editor):
    Moment = apps.get_model("social", "CoupleMomentModel")
    Membership = apps.get_model("social", "CoupleMembershipModel")
    alias = schema_editor.connection.alias
    for moment in Moment.objects.using(alias).all().iterator():
        members = list(Membership.objects.using(alias).filter(couple_id=moment.couple_id)
                   .order_by("id").values_list("id", "user_id"))
        Moment.objects.using(alias).filter(pk=moment.pk).update(
            expires_at=moment.created_at + timedelta(hours=24), audience_membership_ids=[m[0] for m in members],
            audience_user_ids=[m[1] for m in members])


class Migration(migrations.Migration):
    dependencies = [("social", "0007_couple_profile_memberships"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="couplemomentmodel", name="expires_at",
            field=models.DateTimeField(null=True, editable=False, db_index=True)),
        migrations.AddField(model_name="couplemomentmodel", name="audience_membership_ids",
            field=models.JSONField(default=list, editable=False)),
        migrations.AddField(model_name="couplemomentmodel", name="audience_user_ids",
            field=models.JSONField(default=list, editable=False)),
        migrations.RunPython(backfill_moments, migrations.RunPython.noop),
        migrations.AlterField(model_name="couplemomentmodel", name="expires_at",
            field=models.DateTimeField(editable=False, db_index=True)),
        migrations.AlterField(model_name="couplemomentmodel", name="created_at",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False)),
        migrations.AlterModelOptions(name="couplemomentmodel", options={"ordering": ["-created_at", "-id"],
            "verbose_name": "Couple Moment", "verbose_name_plural": "Couple Moments"}),
        migrations.AlterModelOptions(name="couplemomentphotomodel", options={"ordering": ["order", "id"]}),
        migrations.AddIndex(model_name="couplemomentmodel", index=models.Index(
            fields=["couple", "-created_at", "-id"], name="moment_couple_feed_idx")),
        migrations.AddIndex(model_name="couplemomentmodel", index=models.Index(
            fields=["-created_at", "-id"], name="moment_feed_idx")),
        migrations.CreateModel(name="MomentFileDeletion", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=500, unique=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ]),
        migrations.CreateModel(name="CoupleMomentNoteModel", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("message", models.CharField(max_length=1000)),
            ("recipient_membership_ids", models.JSONField(default=list, editable=False)),
            ("recipient_user_ids", models.JSONField(default=list, editable=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("moment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notes", to="social.couplemomentmodel")),
            ("sender", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="moment_notes", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-created_at", "-id"]}),
    ]
