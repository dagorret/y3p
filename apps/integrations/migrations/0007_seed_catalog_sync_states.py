"""Inicializa la agenda semanal sin ejecutar llamadas a proveedores."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def seed_agendas(apps, schema_editor):
    """Crea una agenda por origen para el próximo sábado a la 01:00 local."""
    State = apps.get_model('integrations', 'CatalogSyncState')
    zone = ZoneInfo(settings.TIME_ZONE)
    now = timezone.now().astimezone(zone)
    for source in ('TEAMS', 'MOODLE'):
        state, _ = State.objects.using(schema_editor.connection.alias).get_or_create(source=source)
        date = now.date() + timedelta(days=(state.schedule_weekday - now.weekday()) % 7)
        candidate = datetime.combine(date, state.schedule_hour, tzinfo=zone)
        #FCATALOGMIGRATION01-9401: Una hora ya alcanzada se desplaza a la semana siguiente.
        state.next_run_at = candidate + timedelta(days=7) if candidate <= now else candidate
        state.save(using=schema_editor.connection.alias, update_fields=['next_run_at'])


class Migration(migrations.Migration):
    dependencies = [('integrations', '0006_moodlecoursemirror_teamsworkspacemirror_and_more')]
    operations = [migrations.RunPython(seed_agendas, migrations.RunPython.noop)]
