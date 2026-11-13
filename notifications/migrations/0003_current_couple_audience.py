import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("notifications", "0002_installation_notification_active_session"), ("social", "0022_merge_notification_compatibility")]
    operations = [
        migrations.AddField(model_name="event", name="couple", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="social.couplemodel")),
        migrations.AddField(model_name="event", name="audience_membership_ids", field=models.JSONField(default=list)),
    ]
