from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("social", "0024_moment_video")]
    operations = [
        migrations.AddField(model_name="couplemembershipmodel", name="partner_nickname",
            field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="couplemembershipmodel", name="partner_nickname_revision",
            field=models.PositiveBigIntegerField(default=1)),
    ]
