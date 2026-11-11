# Generado por Django 6.1.2 el 2026-11-11 21:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0009_alter_sagastatusdefinition_target_platform'),
    ]

    operations = [
        migrations.RenameField(
            model_name='personalprofile',
            old_name='phone_number',
            new_name='phone',
        ),
        migrations.AddField(
            model_name='identity',
            name='metadata',
            field=models.JSONField(blank=True, default=dict, help_text='Atributos libres: observaciones, legajo provisorio, número de trámite, etc.'),
        ),
    ]
