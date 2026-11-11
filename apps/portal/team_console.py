"""Consola local de cátedras y operaciones explícitas de conciliación."""

from dataclasses import asdict

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone

from apps.integrations.dataclasses import ExternalActionResult
from apps.integrations.exceptions import IntegrationError, ResourceNotFoundError
from apps.integrations.factories import build_graph, build_moodle, build_sial
from apps.integrations.models import TeamWorkspace
from apps.people.models import Identity
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL
from apps.people.support import require_permission
from .global_services import _team_log


def _norm(value):
    """Compara comisiones y UPN sin espacios exteriores ni diferencias de caja.

    Se conservan los separadores interiores para no mezclar comisiones distintas.
    La correspondencia entre equipo y curso es explícita: no se deduce del nombre.
    """
    return value.strip().casefold()


def _students(participants, commission):
    """Selecciona estudiantes y su comisión sin convertir docentes en miembros.

    El rol Moodle 5 identifica estudiantes en esta instalación. Los propietarios
    de Teams se conservan fuera de la comparación; una igualdad de cantidades
    por sí sola no acredita paridad: se contrastan los conjuntos de UPN.
    """
    return [participant for participant in participants if 5 in participant.role_ids
            and (not commission or any(_norm(group) == _norm(commission) for group in participant.groups))]


def team_grid_context(params, *, feedback='', error=False, team_id=None):
    """Construye la consola desde tablas locales o instantáneas explícitas.

    Las asociaciones y matrículas se consultan en lotes, evitando una consulta
    por equipo. Sin observación o mapeo, el dato es desconocido y no cero. Las
    marcas independientes de Teams y Moodle impiden acreditar una lectura de
    matrículas que solo haya actualizado miembros del equipo.
    """
    queryset = TeamWorkspace.objects.all()
    if team_id is not None:
        queryset = queryset.filter(pk=team_id)
    page = Paginator(queryset, 50).get_page(params.get('page'))
    teams = list(page.object_list)
    mock_ids, members, enrollments, course_names = set(), {}, {}, set()
    if settings.ENABLE_LOCAL_MOCKS and apps.is_installed('apps.platform_mocks'):
        Team = apps.get_model('platform_mocks', 'MockTeam')
        Member = apps.get_model('platform_mocks', 'MockTeamMember')
        Enrollment = apps.get_model('platform_mocks', 'MockMoodleEnrollment')
        Course = apps.get_model('platform_mocks', 'MockMoodleCourse')
        mock_ids = set(Team.objects.filter(pk__in=[team.pk for team in teams]).values_list('pk', flat=True))
        for member in Member.objects.filter(team_id__in=mock_ids).values('team_id', 'user__upn', 'user__first_name', 'user__last_name', 'is_owner'):
            members.setdefault(member['team_id'], []).append({'upn': member['user__upn'],
                'display_name': f"{member['user__first_name']} {member['user__last_name']}".strip(), 'is_owner': member['is_owner']})
        names = [team.moodle_shortname for team in teams if team.pk in mock_ids and team.moodle_shortname]
        course_names = set(Course.objects.filter(shortname__in=names).values_list('shortname', flat=True))
        for enrollment in Enrollment.objects.filter(course__shortname__in=names, role_id=5, user__suspended=False).values('course__shortname', 'user__username', 'group_name'):
            enrollments.setdefault(enrollment['course__shortname'], []).append(enrollment)
    for team in teams:
        if team.pk in mock_ids:
            actual = members.get(team.pk, [])
            expected = [{'upn': row['user__username']} for row in enrollments.get(team.moodle_shortname, [])
                        if not team.commission or _norm(row['group_name'] or '') == _norm(team.commission)]
            known = team.moodle_shortname in course_names
            actual_known = True
        else:
            actual, expected = team.members_snapshot, team.moodle_snapshot
            known = bool(team.moodle_observed_at and team.moodle_shortname)
            actual_known = bool(team.observed_at)
        current = {_norm(member['upn']) for member in actual if not member.get('is_owner')}
        target = {_norm(member['upn']) for member in expected}
        team.console_members = actual
        team.member_count = len(actual) if actual_known else None
        team.moodle_count = len(target) if known else None
        team.parity = 'Normal' if known and actual_known and current == target else ('Discrepancia' if known and actual_known else 'Sin observación / mapeo')
        team.missing_count, team.extra_count = len(target - current), len(current - target)
    return {'teams': teams, 'teams_page': page, 'feedback': feedback, 'error': error}


def _moodle_students(team, moodle):
    if not team.moodle_shortname:
        #FPORTALTEAMCONSOLE001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Configure el shortname Moodle de la cátedra antes de operar.')
    course = moodle.get_course_by_shortname(team.moodle_shortname)
    if course is None:
        #FPORTALTEAMCONSOLE002-8002: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
        raise ResourceNotFoundError('No existe el curso Moodle asociado.')
    return _students(moodle.get_course_participants(int(course['id'])), team.commission)


def refresh_team(team_id, *, user):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    team = TeamWorkspace.objects.get(pk=team_id)
    members = build_graph().get_team_members(str(team.pk))
    students = _moodle_students(team, build_moodle()) if team.moodle_shortname else []
    team.members_snapshot = [asdict(member) for member in members]
    team.moodle_snapshot = [{'upn': student.username, 'display_name': f'{student.first_name} {student.last_name}'.strip()} for student in students]
    team.observed_at = timezone.now()
    owners = sorted(member.upn for member in members if member.is_owner)
    if owners and team.owner_upn not in owners:
        team.owner_upn = owners[0]
    team.moodle_observed_at = team.observed_at if team.moodle_shortname else None
    team.save(update_fields=['members_snapshot', 'moodle_snapshot', 'observed_at', 'moodle_observed_at', 'owner_upn'])
    _team_log(user, 'refresh_team', 'SUCCEEDED', team_id=str(team.pk))


@transaction.atomic
def configure_team(team_id, data, *, user):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    team = TeamWorkspace.objects.select_for_update().get(pk=team_id)
    for key in ('moodle_shortname', 'career_code', 'commission'):
        setattr(team, key, data[key])
    team.observed_at = None
    team.moodle_observed_at = None
    team.moodle_snapshot = []
    team.save(update_fields=['moodle_shortname', 'career_code', 'commission', 'observed_at', 'moodle_snapshot', 'moodle_observed_at'])
    _team_log(user, 'configure_team', 'SUCCEEDED', team_id=str(team.pk))


def populate_team(team_id, data, *, user):
    """Agrega faltantes desde un origen aprobado sin retirar miembros existentes.

    Valida el origen y sus UPN antes de escribir. Un fallo remoto puede dejar
    altas parciales: la bitácora conserva el avance y repetir la operación
    consulta miembros existentes para no duplicarlos. No mantiene una transacción
    de PostgreSQL abierta mientras espera respuestas de los proveedores.
    """
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    if data.get('source') not in ('moodle', 'sial'):
        #FPORTALTEAMCONSOLE003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Seleccione un origen válido.')
    team = TeamWorkspace.objects.get(pk=team_id)
    _team_log(user, 'populate_team', 'STARTED', team_id=str(team.pk), source=data['source'])
    added = 0
    try:
        graph = build_graph()
        if data['source'] == 'moodle':
            students = _moodle_students(team, build_moodle())
            candidates = {student.username for student in students}
        else:
            if not team.career_code:
                #FPORTALTEAMCONSOLE004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                raise ValidationError('Configure la carrera SIAL asociada a esta cátedra.')
            rows = build_sial().get_student_lists(data['campaign'])['ingresantes']
            rows = [row for row in rows if row.career_code == team.career_code and
                    (not team.commission or _norm(row.commission) == _norm(team.commission))]
            identities = dict(Identity.objects.filter(identifier__in=[row.dni for row in rows]).values_list('identifier', 'institutional_upn'))
            candidates = {identities.get(row.dni) or f'a{row.dni}@eco.unrc.edu.ar' for row in rows}
        if any(not _norm(upn).endswith('@eco.unrc.edu.ar') for upn in candidates):
            #FPORTALTEAMCONSOLE005-8005: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('El origen contiene alumnos sin un UPN institucional compatible.')
        current = {_norm(member.upn) for member in graph.get_team_members(str(team.pk))}
        simulated = bool(getattr(graph, 'dry_run', False))
        for upn in sorted(candidates):
            if _norm(upn) in current:
                continue
            result = graph.add_team_member(str(team.pk), upn)
            if not result.success:
                #FPORTALTEAMCONSOLE006-8006: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError('No se confirmó el alta de un miembro del equipo.')
            simulated = simulated or result.simulated
            added += result.affected_count
        if not simulated:
            members = graph.get_team_members(str(team.pk))
            team.members_snapshot = [asdict(member) for member in members]
            team.observed_at = timezone.now()
            fields = ['members_snapshot', 'observed_at']
            if data['source'] == 'moodle':
                team.moodle_snapshot = [{'upn': student.username} for student in students]
                team.moodle_observed_at = team.observed_at
                fields += ['moodle_snapshot', 'moodle_observed_at']
            team.save(update_fields=fields)
        _team_log(user, 'populate_team', 'SIMULATED' if simulated else 'SUCCEEDED',
                  team_id=str(team.pk), affected_count=added, source=data['source'])
        return ExternalActionResult('populate_team', simulated=simulated, affected_count=added)
    #FPORTALTEAMCONSOLE007-8007: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Exception as error:
        _team_log(user, 'populate_team', 'FAILED', team_id=str(team.pk), affected_count=added, error_type=type(error).__name__)
        #FPORTALTEAMCONSOLE008-8008: Propaga el fallo original sin convertirlo en éxito.
        raise
