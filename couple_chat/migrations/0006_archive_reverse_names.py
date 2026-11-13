from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("couple_chat", "0005_spark_sparkanswer")]
    operations = [migrations.AlterField(model_name="sparkanswer", name="actor", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="legacy_spark_answers", to=settings.AUTH_USER_MODEL))]
