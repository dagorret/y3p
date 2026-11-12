# Generado por Django 6.1.2 el 2026-11-12 04:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='adminquerysnippet',
            name='scope',
            field=models.CharField(choices=[('GRADO', 'Grado'), ('POSGRADO', 'Posgrado'), ('EXTENSION', 'Extensión')], db_index=True, default='GRADO', max_length=10, verbose_name='Ámbito académico'),
        ),
    ]
