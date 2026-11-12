# Generado por Django 6.1.2 el 2026-11-12 04:20

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0014_digitalaccount_observations_outboxevent_requested_by_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='identity',
            options={'permissions': [('can_view_student_360', 'Puede consultar la Ficha 360°, vinculaciones, cuentas e historial'), ('can_operate_student_daily', 'Puede ejecutar soporte diario de alumnos'), ('can_administrate_student_critical', 'Puede ejecutar administración crítica de alumnos'), ('can_view_posgrado', 'Puede consultar Posgrado'), ('can_operate_posgrado', 'Puede operar Posgrado'), ('can_admin_posgrado', 'Puede administrar Posgrado'), ('can_view_extension', 'Puede consultar Extensión'), ('can_operate_extension', 'Puede operar Extensión'), ('can_admin_extension', 'Puede administrar Extensión')], 'verbose_name': 'Identidad', 'verbose_name_plural': 'Identidades'},
        ),
    ]
