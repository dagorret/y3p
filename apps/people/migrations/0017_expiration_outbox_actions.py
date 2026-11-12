"""Hace explícitas las suspensiones de TTL de preinscripciones de Grado."""

from django.db import migrations


def configure_expiration_actions(apps, schema_editor):
    Status = apps.get_model('people', 'SagaStatusDefinition')
    #FTTLMIGRATION01-10601: Los estados de baja por TTL también deben persistir una intención externa.
    Status.objects.using(schema_editor.connection.alias).filter(profile_type__code='ALUMNO',
        code__in=['BAJA_PRE', 'BAJA_ASP', 'BAJA_INGRE']).update(provisioning_action='SUSPEND')


class Migration(migrations.Migration):
    dependencies = [('people', '0016_scoped_academic_roles_and_transitions')]
    operations = [migrations.RunPython(configure_expiration_actions, migrations.RunPython.noop)]
