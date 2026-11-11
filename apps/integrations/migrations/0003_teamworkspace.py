# Generado por Django 6.1.2 el 2026-11-11 22:25

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0002_seed_admission_course_rules'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamWorkspace',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('display_name', models.CharField(max_length=255)),
                ('owner_upn', models.EmailField(max_length=150)),
                ('moodle_shortname', models.CharField(blank=True, max_length=100)),
                ('career_code', models.CharField(blank=True, max_length=20)),
                ('commission', models.CharField(blank=True, max_length=100)),
                ('members_snapshot', models.JSONField(blank=True, default=list)),
                ('moodle_snapshot', models.JSONField(blank=True, default=list)),
                ('observed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['display_name', 'id'],
            },
        ),
    ]
