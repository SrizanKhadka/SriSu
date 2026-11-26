from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('social', '0025_private_partner_nickname'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='CoupleFollowVersion', fields=[
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to=settings.AUTH_USER_MODEL)),
            ('version', models.PositiveBigIntegerField(default=0)),
        ]),
        migrations.CreateModel(name='CouplePublicFollow', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('identity_version', models.PositiveBigIntegerField()),
            ('target_audience', models.JSONField(default=list)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('couple', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_followers', to='social.couplemodel')),
            ('source_membership', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_follows', to='social.couplemembershipmodel')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_couple_follows', to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['-created_at', '-id'],
            'indexes': [models.Index(fields=['couple', '-created_at', '-id'], name='public_follow_incoming_idx')],
            'constraints': [models.UniqueConstraint(fields=('user', 'couple'), name='unique_public_couple_follow')]}),
    ]
