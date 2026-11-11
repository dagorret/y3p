# Generado por Django 6.1.2 el 2026-11-11 22:12

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0011_seed_manual_administration_profiles'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='identity',
            options={'permissions': [('can_view_student_360', 'Puede consultar la Ficha 360°, vinculaciones, cuentas e historial'), ('can_operate_student_daily', 'Puede ejecutar soporte diario de alumnos'), ('can_administrate_student_critical', 'Puede ejecutar administración crítica de alumnos')], 'verbose_name': 'Identidad', 'verbose_name_plural': 'Identidades'},
        ),
    ]
