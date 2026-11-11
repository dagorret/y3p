"""Recupera el inventario conocido de equipos creados desde el portal."""

import json
from uuid import UUID

from django.db import migrations


def backfill_workspaces(apps, schema_editor):
    Workspace = apps.get_model('integrations', 'TeamWorkspace')
    Log = apps.get_model('admin', 'LogEntry')
    database = schema_editor.connection.alias
    # La bitácora histórica no conservaba owner; se completa desde mocks si existe.
    try:
        Team = apps.get_model('platform_mocks', 'MockTeam')
    #FINTEGRATIONSMIGRATIONSBACKFILLTEAMWORKSPACES001-8001: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except LookupError:
        Team = None
    if Team is not None:
        for team in Team.objects.using(database).select_related('owner').iterator():
            Workspace.objects.using(database).get_or_create(pk=team.pk, defaults={
                'display_name': team.display_name, 'owner_upn': team.owner.upn,
            })
    for entry in Log.objects.using(database).filter(content_type__isnull=True).order_by('pk').iterator():
        try:
            data = json.loads(entry.change_message)
            if not isinstance(data, dict) or data.get('action') != 'create_team' or data.get('status') not in ('SUCCEEDED', 'ACCEPTED'):
                continue
            team_id = UUID(data['team_id'])
        #FINTEGRATIONSMIGRATIONSBACKFILLTEAMWORKSPACES002-8002: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except (ValueError, TypeError, KeyError):
            continue
        Workspace.objects.using(database).get_or_create(pk=team_id, defaults={
            'display_name': data.get('name') or str(team_id), 'owner_upn': '',
        })


class Migration(migrations.Migration):
    dependencies = [('integrations', '0004_teamworkspace_moodle_observed_at'), ('admin', '0003_logentry_add_action_flag_choices')]
    operations = [migrations.RunPython(backfill_workspaces, migrations.RunPython.noop)]
