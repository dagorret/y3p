"""Altas y operaciones globales con permisos, transacciones y bitácora."""

import json
import re
from uuid import UUID

from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.integrations.exceptions import IntegrationError
from apps.integrations.factories import build_graph
from apps.integrations.models import TeamWorkspace
from apps.people.models import ConfigurationError, Identity, PersonalProfile, ProfileTypeDefinition
from apps.people.services import create_affiliation
from apps.people.support import audit, require_permission
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL


@transaction.atomic
def create_manual_identity(data, *, user, organizational=False):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    profile_code = 'INSTITUCIONAL' if organizational else data['role']
    if profile_code not in ('INSTITUCIONAL', 'ALUMNO', 'STAFF'):
        #FPORTALGLOBALSERVICES001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Rol de alta no permitido.')
    identifier = data['identifier'] if organizational else data['dni']
    if Identity.objects.filter(identifier=identifier).exists():
        #FPORTALGLOBALSERVICES002-8002: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('La identidad ya existe. Utilice el buscador y su Ficha 360°.')
    upn = data['institutional_upn'] if organizational else f'a{identifier}@eco.unrc.edu.ar'
    if Identity.objects.filter(institutional_upn__iexact=upn).exists():
        #FPORTALGLOBALSERVICES003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('El UPN ya está asociado a otra identidad.')
    profile = ProfileTypeDefinition.objects.get(code=profile_code)
    if profile.is_organizational != organizational:
        #FPORTALGLOBALSERVICES004-8004: Origina ConfigurationError; el llamador conserva la causa y decide su tratamiento.
        raise ConfigurationError('El tipo de vinculación no corresponde a la categoría de identidad.')
    # El alta directa de alumno es un ingresante, no una preinscripción.
    status_code = 'INGRE' if profile_code == 'ALUMNO' else profile.initial_status.code
    status = profile.statuses.get(code=status_code)
    if status.provisioning_action != 'PROVISION':
        #FPORTALGLOBALSERVICES005-8005: Origina ConfigurationError; el llamador conserva la causa y decide su tratamiento.
        raise ConfigurationError('El estado de alta no tiene aprovisionamiento configurado.')
    identity = Identity(
        category=Identity.Category.ORGANIZACIONAL if organizational else Identity.Category.FISICA,
        identifier=identifier, first_name=data['first_name'],
        last_name='' if organizational else data['last_name'], institutional_upn=upn,
    )
    identity.full_clean()
    identity.save()
    contact = PersonalProfile(identity=identity, personal_email=data['personal_email'])
    contact.full_clean()
    contact.save()
    affiliation = create_affiliation(
        identity, profile, initial_status_code=status_code, reference=data['reference'],
        changed_by=f'operador:{user.get_username()}', user=user,
        reason='Alta directa de cuenta funcional' if organizational else 'Alta manual de persona',
    )
    audit(identity, user, 'functional_create' if organizational else 'manual_create',
          status='SUCCEEDED', affiliation_id=affiliation.pk, profile=profile_code)
    return identity


def _team_id(resource):
    if not resource:
        return None
    try:
        return str(UUID(resource))
    #FPORTALGLOBALSERVICES006-8006: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except ValueError:
        match = re.search(r"teams(?:/|\(')([a-fA-F0-9-]{36})", resource)
        return str(UUID(match[1])) if match else None


def _team_log(user, action, status, *, team_id=None, **details):
    LogEntry.objects.create(
        user=user, action_flag=CHANGE, object_id=team_id or '', object_repr=f'Teams: {team_id or "nuevo equipo"}',
        change_message=json.dumps({'module': 'teams', 'action': action, 'status': status,
                                   'team_id': team_id, **details}, ensure_ascii=True),
    )


def team_operation(action, data, *, user):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    if action not in ('create_team', 'purge_team'):
        #FPORTALGLOBALSERVICES007-8007: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Operación Teams no permitida.')
    team_id = str(data['team_id']) if action == 'purge_team' else None
    _team_log(user, action, 'STARTED', team_id=team_id)
    try:
        graph = build_graph()
        if action == 'create_team':
            result = graph.create_team_from_template(data['display_name'], data['description'], data['owner_upn'])
            team_id = _team_id(result.resource_id)
        else:
            # El cliente admite otras políticas, pero el portal conserva siempre propietarios.
            if not data.get('confirm'):
                #FPORTALGLOBALSERVICES008-8008: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                raise ValidationError('Debe confirmar la limpieza.')
            result = graph.purge_team_members(team_id, keep_owners=True)
        if not result.success:
            #FPORTALGLOBALSERVICES009-8009: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
            raise IntegrationError('Graph no confirmó la operación Teams.')
        status = 'SIMULATED' if result.simulated else ('ACCEPTED' if result.accepted else 'SUCCEEDED')
        if action == 'create_team' and team_id and not result.simulated:
            TeamWorkspace.objects.update_or_create(pk=team_id, defaults={
                'display_name': data['display_name'], 'owner_upn': data['owner_upn'],
                'moodle_shortname': data.get('moodle_shortname', ''),
                'career_code': data.get('career_code', ''), 'commission': data.get('commission', ''),
            })
        elif action == 'purge_team' and not result.simulated:
            TeamWorkspace.objects.filter(pk=team_id).update(observed_at=None, members_snapshot=[])
        _team_log(user, action, status, team_id=team_id, affected_count=result.affected_count,
                  name=data.get('display_name', ''))
        return result
    #FPORTALGLOBALSERVICES010-8010: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Exception as error:
        _team_log(user, action, 'FAILED', team_id=team_id, error_type=type(error).__name__)
        #FPORTALGLOBALSERVICES011-8011: Propaga el fallo original sin convertirlo en éxito.
        raise


def recent_team_operations():
    entries = LogEntry.objects.filter(content_type__isnull=True, change_message__contains='"module": "teams"').select_related('user')[:30]
    operations = []
    for entry in entries:
        data = json.loads(entry.change_message)
        operations.append({**data, 'user': entry.user, 'at': entry.action_time})
    return operations
