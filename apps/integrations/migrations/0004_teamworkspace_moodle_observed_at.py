# Generado por Django 6.1.2 el 2026-11-11 22:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0003_teamworkspace'),
    ]

    operations = [
        migrations.AddField(
            model_name='teamworkspace',
            name='moodle_observed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
