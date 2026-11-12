# Generado por Django 6.1.2 el 2026-11-12 04:03

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('platform_mocks', '0003_mockazureuser_last_activity_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='MockTeamChannel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel_id', models.CharField(max_length=255, unique=True, verbose_name='Identificador del canal')),
                ('display_name', models.CharField(max_length=255, verbose_name='Nombre')),
                ('membership_type', models.CharField(choices=[('standard', 'Estándar'), ('private', 'Privado')], max_length=20, verbose_name='Tipo de membresía')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channels', to='platform_mocks.mockteam', verbose_name='Equipo')),
            ],
            options={
                'verbose_name': 'Canal simulado',
                'verbose_name_plural': 'Canales simulados',
                'constraints': [models.CheckConstraint(condition=models.Q(('membership_type__in', ['standard', 'private'])), name='ck_mock_channel_type')],
            },
        ),
    ]
