"""Matriz histórica de ingreso: tres carreras comparten reglas de comisión."""

from django.db import migrations


def seed_rules(apps, schema_editor):
    Rule = apps.get_model('integrations', 'AdmissionCourseRule')
    rules = Rule.objects.using(schema_editor.connection.alias)
    for career in ('2', '3', '4'):
        for order, commission, courses in (
            (10, 'COMISION 1', 'I3,I4,I5,I6,I1'),
            (20, 'COMISION 2', 'I3,I4,I5,I6,I2'),
            (30, None, 'I3,I4,I5,I6'),
        ):
            rules.get_or_create(
                order=order, career_code=career, modality=None, commission_pattern=commission,
                defaults={'target_courses': courses, 'target_group': commission, 'is_active': True},
            )
    for order, career, course in ((40, '7', 'I7'), (50, '8', 'I8')):
        rules.get_or_create(
            order=order, career_code=career, modality=None, commission_pattern=None,
            defaults={'target_courses': course, 'target_group': None, 'is_active': True},
        )


class Migration(migrations.Migration):
    dependencies = [('integrations', '0001_initial')]
    # Preserva la configuración administrativa en un eventual retroceso.
    operations = [migrations.RunPython(seed_rules, migrations.RunPython.noop)]
