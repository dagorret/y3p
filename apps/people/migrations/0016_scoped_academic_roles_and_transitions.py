"""Permisos por ámbito y grafo explícito de confirmación y vencimiento académico."""

from django.db import migrations


def seed_scopes(apps, schema_editor):
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Profile = apps.get_model('people', 'ProfileTypeDefinition')
    Status = apps.get_model('people', 'SagaStatusDefinition')
    Rule = apps.get_model('people', 'SagaTransitionRule')
    db = schema_editor.connection.alias
    content_type, _ = ContentType.objects.using(db).get_or_create(app_label='people', model='identity')
    for scope, label in (('POSGRADO', 'Posgrado'), ('EXTENSION', 'Extensión')):
        permissions = []
        for level, role, verb in (('view', 'jefe', 'consultar'), ('operate', 'operador', 'operar'), ('admin', 'admin', 'administrar')):
            permission, _ = Permission.objects.using(db).get_or_create(content_type=content_type,
                codename=f'can_{level}_{scope.lower()}', defaults={'name': f'Puede {verb} {label}'})
            permissions.append(permission)
            group, _ = Group.objects.using(db).get_or_create(name=f'adm_{scope.lower()}_rol_{role}')
            group.permissions.set(permissions)
        profile = Profile.objects.using(db).get(code=scope)
        statuses = {}
        for code, name, terminal, ttl, action in (
            ('PRE', 'Preinscripto', False, 60, ''), ('ALUMNO', 'Alumno confirmado', False, 365, 'PROVISION'),
            ('BAJA_PRE', 'Preinscripción vencida', True, None, 'SUSPEND'), ('BAJA', 'Vinculación vencida', True, None, 'SUSPEND')):
            statuses[code], _ = Status.objects.using(db).update_or_create(profile_type=profile, code=code,
                defaults={'name': name, 'is_terminal': terminal, 'default_ttl_days': ttl, 'provisioning_action': action})
        for source, target, expiration in (('PRE', 'ALUMNO', False), ('PRE', 'BAJA_PRE', True),
                                           ('ALUMNO', 'BAJA', True), ('REGULAR', 'BAJA', True)):
            origin = Status.objects.using(db).get(profile_type=profile, code=source)
            #FSCOPEDMIGRATION01-10004: Cada salida académica pertenece al mismo ámbito y tiene regla explícita.
            Rule.objects.using(db).get_or_create(profile_type=profile, from_status=origin, to_status=statuses[target],
                defaults={'action_name': 'Vencimiento automático' if expiration else 'Confirmar preinscripción',
                          'is_expiration': expiration, 'requires_cross_validation': False})


class Migration(migrations.Migration):
    dependencies = [('people', '0015_alter_identity_options'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(seed_scopes, migrations.RunPython.noop)]
