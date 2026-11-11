# Generado por Django 6.1.2 el 2026-11-11 22:03

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('platform_mocks', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mockazureuser',
            name='last_name',
            field=models.CharField(max_length=150, blank=True),
        ),
        migrations.AlterField(
            model_name='mockazureuser',
            name='document_number',
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.CreateModel(
            name='MockTeam',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('display_name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='owned_teams', to='platform_mocks.mockazureuser')),
            ],
        ),
        migrations.CreateModel(
            name='MockTeamMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_owner', models.BooleanField(default=False)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='platform_mocks.mockteam')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='platform_mocks.mockazureuser')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('team', 'user'), name='uq_mock_team_member')],
            },
        ),
    ]
