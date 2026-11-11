"""Perfiles de alta directa con emisión de aprovisionamiento."""

from django.db import migrations


def seed_manual_profiles(apps, schema_editor):
    Profile = apps.get_model('people', 'ProfileTypeDefinition')
    Status = apps.get_model('people', 'SagaStatusDefinition')
    database = schema_editor.connection.alias
    staff, _ = Profile.objects.using(database).get_or_create(
        code='STAFF', defaults={'name': 'Personal de la Facultad', 'is_organizational': False, 'allows_multiple_instances': False},
    )
    if not Status.objects.using(database).filter(profile_type=staff, is_initial=True).exists():
        Status.objects.using(database).get_or_create(profile_type=staff, code='ACTIVO', defaults={
            'name': 'Servicio activo', 'is_initial': True, 'provisioning_action': 'PROVISION',
            'target_platform': 'OTRO', 'requires_collaboration_account': True, 'requires_collaboration_license': True,
        })
    # La cuenta funcional no consume una licencia A1 por defecto.
    Status.objects.using(database).filter(
        profile_type__code='INSTITUCIONAL', code='ACTIVA', provisioning_action='',
    ).update(provisioning_action='PROVISION', target_platform='OTRO')


class Migration(migrations.Migration):
    dependencies = [('people', '0010_identity_metadata_personalprofile_phone')]
    # Conserva configuración y referencias administrativas al retroceder.
    operations = [migrations.RunPython(seed_manual_profiles, migrations.RunPython.noop)]
