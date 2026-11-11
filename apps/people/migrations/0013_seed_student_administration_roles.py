"""Roles institucionales del portal con permisos creados antes de post_migrate."""

from django.db import migrations


CAPABILITIES = (
    ('can_view_student_360', 'Puede consultar la Ficha 360°, vinculaciones, cuentas e historial'),
    ('can_operate_student_daily', 'Puede ejecutar soporte diario de alumnos'),
    ('can_administrate_student_critical', 'Puede ejecutar administración crítica de alumnos'),
)


def seed_administration_roles(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    database = schema_editor.connection.alias
    content_types = {}
    for model in ('identity', 'affiliation', 'personalprofile'):
        content_types[model], _ = ContentType.objects.using(database).get_or_create(app_label='people', model=model)
    permissions = []
    for model, label in (('identity', 'Identidad'), ('affiliation', 'Vinculación'), ('personalprofile', 'Perfil Personal')):
        permission, _ = Permission.objects.using(database).get_or_create(
            content_type=content_types[model], codename=f'view_{model}', defaults={'name': f'Puede consultar {label}'},
        )
        permissions.append(permission)
    for index, (codename, name) in enumerate(CAPABILITIES):
        permission, _ = Permission.objects.using(database).get_or_create(
            content_type=content_types['identity'], codename=codename, defaults={'name': name},
        )
        permissions.append(permission)
        role = ('jefe', 'operador', 'admin')[index]
        group, _ = Group.objects.using(database).get_or_create(name=f'adm_alumnos_rol_{role}')
        group.permissions.set(permissions)


class Migration(migrations.Migration):
    dependencies = [
        ('people', '0012_alter_identity_options'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]
    # Un retroceso no elimina grupos con usuarios asignados ni sus permisos.
    operations = [migrations.RunPython(seed_administration_roles, migrations.RunPython.noop)]
