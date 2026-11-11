# Generado por Django 6.1.2 el 2026-11-11 22:25

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0013_seed_student_administration_roles'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='digitalaccount',
            name='observations',
            field=models.JSONField(blank=True, default=dict, help_text='Observaciones locales: licencia, cambio de clave, última actividad y cursos. Sin secretos.'),
        ),
        migrations.AddField(
            model_name='outboxevent',
            name='requested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='requested_outbox_events', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='outboxevent',
            name='action',
            field=models.CharField(choices=[('PROVISION', 'Aprovisionar'), ('SUSPEND', 'Suspender'), ('TERMINATE', 'Dar de baja'), ('SUPPORT', 'Soporte administrativo')], max_length=16),
        ),
    ]
