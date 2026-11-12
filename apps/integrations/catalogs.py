"""Espejos relacionales de catálogos con reemplazo atómico por origen.

TeamsMemberMirror es una tabla pivot: permite JOIN, EXISTS, unicidad e índices
por identidad, equipo y rol. JSON no conserva estas garantías ni permite los
mismos planes analíticos. Las llamadas remotas ocurren antes de la transacción;
solo una lectura completa permite retirar filas ausentes. Un arrendamiento con
identificador de ejecución evita que un trabajador atrasado reemplace datos de
otro. No se consultan registros de acceso: esa evidencia pertenece a Purview.
"""

import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.integrations.exceptions import IntegrationError
from apps.integrations.factories import build_graph, build_moodle
from apps.people.models import DigitalAccount, Identity
from .models import CatalogSyncState, MoodleCourseMirror, TeamsChannelMirror, TeamsMemberMirror, TeamsWorkspaceMirror, TeamWorkspace


SOURCES = ('TEAMS', 'MOODLE')


def next_scheduled_run(state, *, now=None):
    """Calcula el próximo día y hora en la zona horaria institucional."""
    now = timezone.localtime(now or timezone.now())
    date = now.date() + timedelta(days=(state.schedule_weekday - now.weekday()) % 7)
    candidate = timezone.make_aware(datetime.combine(date, state.schedule_hour), now.tzinfo)
    return candidate + timedelta(days=7) if candidate <= now else candidate


def ensure_sync_states():
    """Inicializa agendas faltantes sin alterar la programación ya configurada."""
    for source in SOURCES:
        state, _ = CatalogSyncState.objects.get_or_create(source=source)
        if state.next_run_at is None:
            CatalogSyncState.objects.filter(pk=state.pk, next_run_at=None).update(next_run_at=next_scheduled_run(state))
    return list(CatalogSyncState.objects.order_by('source'))


def catalog_clients():
    """Selecciona adaptadores locales explícitos cuando están habilitados los mocks."""
    if settings.ENABLE_LOCAL_MOCKS:
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient
        return MockGraphClient(), MockMoodleClient()
    return build_graph(), build_moodle()


def _complete_teams(graph, teams):
    """Materializa equipos, canales y roles antes de modificar el espejo."""
    result = []
    ids, channel_ids = set(), set()
    for team in teams:
        #FCATALOG01-9101: Identificadores ausentes o repetidos invalidan el origen completo.
        if not isinstance(team, dict) or not team.get('id') or not team.get('displayName') or str(team['id']) in ids:
            raise IntegrationError('El catálogo Teams contiene equipos inválidos o duplicados.')
        team_id = str(team['id'])
        ids.add(team_id)
        channels = list(graph.get_team_channels(team_id))
        members = list(graph.get_team_members(team_id))
        upns = set()
        for channel in channels:
            #FCATALOG02-9102: No se confirma una página incompleta de canales.
            if not isinstance(channel, dict) or not channel.get('id') or not channel.get('displayName') or channel['id'] in channel_ids or channel.get('membershipType') not in {'standard', 'private', 'shared'}:
                raise IntegrationError('El catálogo Teams contiene canales inválidos o duplicados.')
            channel_ids.add(channel['id'])
        for member in members:
            #FCATALOG03-9103: La unicidad se verifica con el mismo UPN normalizado que se persiste.
            upn = member.upn.strip().casefold()
            if not upn or not member.user_id or upn in upns:
                raise IntegrationError('El catálogo Teams contiene miembros inválidos o duplicados.')
            upns.add(upn)
        result.append((team, channels, members))
    return result


def _identity_maps(rows):
    upns = {member.upn.strip().casefold() for _, _, members in rows for member in members}
    azure_ids = {member.user_id for _, _, members in rows for member in members}
    identities = dict(Identity.objects.annotate(normalized_upn=Lower('institutional_upn')).filter(normalized_upn__in=upns).values_list('normalized_upn', 'pk'))
    accounts = {}
    for account in DigitalAccount.objects.filter(Q(account_identifier__in=azure_ids) | Q(observations__azure_user_id__in=list(azure_ids)), platform='TEAMS'):
        accounts[account.account_identifier] = account.identity_id
        if account.observations.get('azure_user_id'):
            accounts[account.observations['azure_user_id']] = account.identity_id
    return identities, accounts


def _save_teams(rows, now):
    """Actualiza la pivot y sus contadores dentro de la transacción del llamador."""
    identities, accounts = _identity_maps(rows)
    mappings = {str(team.pk): team for team in TeamWorkspace.objects.all()}
    for team, channels, members in sorted(rows, key=lambda row: str(row[0]['id'])):
        team_id = str(team['id'])
        mapping = mappings.get(team_id)
        defaults = {'display_name': team['displayName'], 'mail_nickname': team.get('mailNickname') or '',
                    'description': team.get('description') or '', 'owners_count': sum(m.is_owner for m in members),
                    'members_count': len(members), 'synced_at': now}
        if mapping:
            defaults.update(career_code=mapping.career_code, commission=mapping.commission)
        workspace, _ = TeamsWorkspaceMirror.objects.update_or_create(team_id=team_id, defaults=defaults)
        channel_rows = [TeamsChannelMirror(channel_id=str(channel['id']), workspace=workspace,
            display_name=channel['displayName'], membership_type=channel['membershipType'], synced_at=now)
            for channel in channels if channel['membershipType'] != 'shared']
        #FCATALOG04-9104: Solo los canales estándar y privados pertenecen al alcance de este espejo.
        TeamsChannelMirror.objects.bulk_create(channel_rows, update_conflicts=True, unique_fields=['channel_id'],
            update_fields=['workspace', 'display_name', 'membership_type', 'synced_at'], batch_size=500)
        workspace.channels.exclude(channel_id__in=[row.channel_id for row in channel_rows]).delete()
        member_rows = []
        for member in members:
            upn = member.upn.strip().casefold()
            member_rows.append(TeamsMemberMirror(workspace=workspace, upn=upn,
                azure_user_id=member.user_id, identity_id=identities.get(upn) or accounts.get(member.user_id),
                display_name=member.display_name, role='owner' if member.is_owner else 'member', synced_at=now))
        TeamsMemberMirror.objects.bulk_create(member_rows, update_conflicts=True, unique_fields=['workspace', 'upn'],
            update_fields=['azure_user_id', 'identity', 'display_name', 'role', 'synced_at'], batch_size=500)
        workspace.members.exclude(upn__in=[row.upn for row in member_rows]).delete()


def _complete_courses(moodle):
    rows, ids = [], set()
    for course in moodle.list_courses():
        #FCATALOG05-9105: Una lectura Moodle incompleta nunca autoriza bajas masivas.
        if not isinstance(course, dict) or not course.get('id') or not course.get('shortname') or not course.get('fullname') or course['id'] in ids:
            raise IntegrationError('El catálogo Moodle contiene cursos inválidos o duplicados.')
        ids.add(course['id'])
        rows.append({**course, 'enrolled_count': len(moodle.get_course_participants(int(course['id'])))})
    return rows


def sync_external_catalogs(*, sources=SOURCES, graph=None, moodle=None):
    """Sincroniza cada origen de forma independiente y registra también los fallos."""
    #FCATALOG06-9106: El origen no puede inyectar modelos ni rutas de integración.
    if not sources or any(source not in SOURCES for source in sources):
        raise ValidationError('Seleccione un origen TEAMS o MOODLE.')
    ensure_sync_states()
    results = {}
    for source in dict.fromkeys(sources):
        token, now = uuid.uuid4(), timezone.now()
        with transaction.atomic():
            state = CatalogSyncState.objects.select_for_update().get(source=source)
            if state.run_token and state.lock_expires_at and state.lock_expires_at > now:
                #FCATALOG07-9107: Las ejecuciones duplicadas no modifican un espejo en actualización.
                results[source] = 'OMITIDO'
                continue
            state.run_token, state.lock_expires_at = token, now + timedelta(minutes=30)
            state.save(update_fields=['run_token', 'lock_expires_at'])
        try:
            if source == 'TEAMS':
                client = graph or catalog_clients()[0]
                #FCATALOG08-9108: Una simulación no confirma ausencia remota ni permite purgar espejos.
                if client.dry_run:
                    raise IntegrationError('La sincronización requiere lecturas confirmadas, sin simulación.')
                rows = _complete_teams(client, client.list_teams())
            else:
                client = moodle or catalog_clients()[1]
                if client.dry_run:
                    #FCATALOG09-9109: No persiste catálogos simulados como observaciones reales.
                    raise IntegrationError('La sincronización requiere lecturas confirmadas, sin simulación.')
                rows = _complete_courses(client)
            with transaction.atomic():
                state = CatalogSyncState.objects.select_for_update().get(source=source)
                if state.run_token != token:
                    #FCATALOG10-9110: Un trabajador atrasado pierde el derecho a confirmar su lectura.
                    results[source] = 'OMITIDO'
                    continue
                now = timezone.now()
                if source == 'TEAMS':
                    _save_teams(rows, now)
                    TeamsWorkspaceMirror.objects.exclude(team_id__in=[str(row[0]['id']) for row in rows]).delete()
                else:
                    for course in rows:
                        MoodleCourseMirror.objects.update_or_create(course_id=course['id'], defaults={
                            'shortname': course['shortname'], 'fullname': course['fullname'],
                            'category_name': course.get('category_name', ''), 'visible': bool(course.get('visible', True)),
                            'enrolled_count': course['enrolled_count'], 'synced_at': now})
                    MoodleCourseMirror.objects.exclude(course_id__in=[row['id'] for row in rows]).delete()
                state.last_run_at, state.last_status, state.items_synced, state.last_error = now, 'SUCCEEDED', len(rows), ''
                state.next_run_at = next_scheduled_run(state, now=now)
                state.run_token, state.lock_expires_at = None, None
                state.save()
            results[source] = 'SUCCEEDED'
        except Exception as error:
            #FCATALOG11-9111: Conserva el espejo anterior y registra un diagnóstico sin payloads ni secretos.
            with transaction.atomic():
                state = CatalogSyncState.objects.select_for_update().get(source=source)
                if state.run_token == token:
                    state.last_status, state.last_run_at, state.items_synced = 'FAILED', timezone.now(), 0
                    state.last_error = f'{type(error).__name__}: No se pudo completar la lectura o actualización del catálogo.'
                    state.next_run_at = next_scheduled_run(state)
                    state.run_token, state.lock_expires_at = None, None
                    state.save()
            results[source] = 'FAILED'
    return results


def sync_due_catalogs():
    """Celery Beat consulta la agenda persistida; no codifica el sábado en la tarea."""
    ensure_sync_states()
    due = list(CatalogSyncState.objects.filter(next_run_at__lte=timezone.now()).values_list('source', flat=True))
    return sync_external_catalogs(sources=due) if due else {}


def refresh_identity_platforms(identity, *, user, graph=None, moodle=None):
    """Refresca solo las pertenencias y cuentas del alumno, sin recopilar accesos."""
    from apps.people.permissions import OPERATE_STUDENT_DAILY
    from apps.people.support import require_permission
    require_permission(user, OPERATE_STUDENT_DAILY)
    if graph is None or moodle is None:
        defaults = catalog_clients()
        graph, moodle = graph or defaults[0], moodle or defaults[1]
    #FCATALOG12-9112: Una identidad sin UPN o clientes simulados no produce evidencia confirmada.
    if not identity.institutional_upn or graph.dry_run or moodle.dry_run:
        raise ValidationError('El refresco requiere UPN institucional y clientes sin simulación.')
    upn = identity.institutional_upn
    remote = graph.get_user(upn)
    status = graph.get_user_state(upn) if remote else {'accountEnabled': False}
    #FCATALOG13-9113: No se interpreta una respuesta sin habilitación como una baja confirmada.
    if not isinstance(status.get('accountEnabled'), bool):
        raise IntegrationError('Graph no confirmó la habilitación de la cuenta.')
    licensed = settings.LUCY_M365_SKU_ID
    if settings.ENABLE_LOCAL_MOCKS and not licensed:
        from apps.platform_mocks import DEV_A1_SKU
        licensed = DEV_A1_SKU
    licenses = graph.get_user_license_ids(upn) if remote else ()
    team_ids = graph.get_user_joined_teams(remote.id) if remote else []
    teams = _complete_teams(graph, [graph.get_team(team_id) for team_id in team_ids])
    moodle_user = moodle.get_user_by_username(upn)
    courses = moodle.get_user_courses(moodle_user.id) if moodle_user else []
    now = timezone.now()
    with transaction.atomic():
        Identity.objects.select_for_update().get(pk=identity.pk)
        stale_ids = list(TeamsMemberMirror.objects.filter(identity=identity).exclude(workspace__team_id__in=team_ids).values_list('workspace_id', flat=True))
        _save_teams(teams, now)
        TeamsMemberMirror.objects.filter(identity=identity).exclude(workspace__team_id__in=team_ids).delete()
        graph_facts = {'azure_user_id': remote.id if remote else None}
        if licensed:
            graph_facts['license_assigned'] = licensed in licenses
        password_profile = status.get('passwordProfile') or {}
        if isinstance(password_profile.get('forceChangePasswordNextSignIn'), bool):
            graph_facts['force_password_change'] = password_profile['forceChangePasswordNextSignIn']
        for platform, active, facts in (
            ('TEAMS', status['accountEnabled'], graph_facts),
            ('MOODLE', moodle_user is not None, {'courses': [course['shortname'] for course in courses]}),
        ):
            account, _ = DigitalAccount.objects.get_or_create(identity=identity, platform=platform, account_identifier=upn)
            account.is_active = active
            account.observations = {**account.observations, **facts, 'observed_at': now.isoformat()}
            account.save()
        stale_workspaces = list(TeamsWorkspaceMirror.objects.filter(pk__in=stale_ids).annotate(
            total=Count('members'), owners=Count('members', filter=Q(members__role='owner'))))
        for workspace in stale_workspaces:
            workspace.members_count, workspace.owners_count = workspace.total, workspace.owners
        TeamsWorkspaceMirror.objects.bulk_update(stale_workspaces, ['members_count', 'owners_count'])
    return now
