from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("authentication", "0022_device_sessions")]
    operations = [migrations.AddField(
        model_name="usermodel", name="share_partner_details",
        field=models.BooleanField(default=False, db_default=False),
    )]
