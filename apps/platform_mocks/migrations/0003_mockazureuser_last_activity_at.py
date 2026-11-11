# Generado por Django 6.1.2 el 2026-11-11 22:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('platform_mocks', '0002_alter_mockazureuser_document_number_mockteam_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='mockazureuser',
            name='last_activity_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
