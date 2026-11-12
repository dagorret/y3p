"""Operaciones de soporte compartidas por Unfold y ventanilla, con bitácora."""

import json
import secrets
import string

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.integrations.dataclasses import ExternalActionResult
from apps.integrations.exceptions import IntegrationError, ResourceNotFoundError
from apps.integrations.factories import build_graph, build_mailer, build_moodle
from .models import AffiliationStatusHistory, Identity
from .permissions import (
    ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY, has_portal_permission,
)


SUPPORT_ACTIONS = {
    'notification': 'Notificación institucional',
    'credentials': 'Reenvío de credenciales',
    'sessions': 'Revocación de sesiones',
    'license': 'Conmutación de licencia A1',
    'suspend': 'Suspensión de identidad',
    'moodle_enrol': 'Inscripción manual Moodle',
    'moodle_unenrol': 'Desmatriculación Moodle',
}


def require_permission(user, permission):
    if permission.startswith('people.can_'):
        allowed = has_portal_permission(user, permission)
    else:
        allowed = user.is_authenticated and user.is_active and user.is_staff and user.has_perm(permission)
    if not allowed:
        #FPEOPLESUPPORT001-8001: Propaga el fallo original sin convertirlo en éxito.
        raise PermissionDenied


def audit(identity, user, action, **details):
    """Admite solo detalles producidos por los servicios, nunca payloads externos."""
    LogEntry.objects.log_actions(
        user_id=user.pk, queryset=[identity], action_flag=CHANGE,
        change_message=json.dumps({'action': action, **details}, ensure_ascii=True),
    )


def _confirm(result):
    if not result.success:
        #FPEOPLESUPPORT002-8002: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
        raise IntegrationError('El proveedor no confirmó la operación.')
    return result


@transaction.atomic
def _observe_existing_accounts(identity, platform, *, course=None, enrolled=None, **facts):
    """Actualiza solo evidencia confirmada de cuentas ya asociadas, sin secretos."""
    for account in identity.digital_accounts.select_for_update().filter(
        platform=platform, account_identifier__iexact=identity.institutional_upn,
    ):
        observations = dict(account.observations) if isinstance(account.observations, dict) else {}
        observations.update(facts)
        observations['observed_at'] = timezone.now().isoformat()
        if course is not None:
            courses = set(observations.get('courses', []))
            if enrolled:
                courses.add(course)
            else:
                courses.discard(course)
            observations['courses'] = sorted(courses)
        account.observations = observations
        account.save(update_fields=['observations', 'updated_at'])


def _password():
    groups = (string.ascii_uppercase, string.ascii_lowercase, string.digits, '!@#$%_-')
    chars = [secrets.choice(group) for group in groups]
    chars.extend(secrets.choice(''.join(groups)) for _ in range(28))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def perform_support(identity_id, action, *, user, dry_run=None, **options):
    """Ejecuta una acción autorizada y registra inicio, resultado y fallo parcial.

    La capacidad se comprueba aquí además de la vista para proteger otros
    llamadores. Los proveedores no participan en la transacción local: cambiar
    una contraseña y fallar al enviar correo exige un resultado parcial y un
    próximo secreto diferente, nunca fingir una reversión remota. La bitácora
    conserva tipos de error y resultados, sin contraseñas ni respuestas crudas.
    """
    if action not in SUPPORT_ACTIONS:
        #FPEOPLESUPPORT003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Acción de soporte inválida.')
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL if action in ('license', 'suspend') else OPERATE_STUDENT_DAILY)
    identity = Identity.objects.get(pk=identity_id)
    audit(identity, user, action, status='STARTED')
    try:
        if not identity.institutional_upn:
            #FPEOPLESUPPORT004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('La identidad no tiene UPN institucional.')
        upn = identity.institutional_upn
        if action == 'notification':
            mailer = build_mailer(dry_run=dry_run) if dry_run is not None else build_mailer()
            #FPEOPLESUPPORT013-8013: El cliente debe respetar la simulación del trabajador.
            if dry_run is not None and bool(mailer.dry_run) != dry_run:
                raise IntegrationError('El cliente no respeta el modo de simulación solicitado.')
            validate_email(options.get('recipient', ''))
            delivery = mailer.send_email(options['recipient'], options['subject'], options['body'])
            #FPEOPLESUPPORT014-8014: Una entrega fallida sigue el circuito de reintentos del Outbox.
            if not delivery.success:
                raise IntegrationError('El proveedor no confirmó la entrega del correo.')
            result = ExternalActionResult(action, simulated=delivery.simulated)
        elif action.startswith('moodle_'):
            result = _moodle_action(identity, action, dry_run=dry_run, **options)
        else:
            graph = build_graph(dry_run=dry_run) if dry_run is not None else build_graph()
            if dry_run is not None and bool(graph.dry_run) != dry_run:
                #FPEOPLESUPPORT005-8005: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError('El cliente no respeta el modo de simulación solicitado.')
            if action == 'credentials':
                profile = getattr(identity, 'personal_profile', None)
                recipient = profile.personal_email if profile else ''
                validate_email(recipient)
                password = _password()
                try:
                    reset = _confirm(graph.reset_password_temporary(upn, password))
                    if not reset.simulated:
                        _observe_existing_accounts(identity, 'TEAMS', force_password_change=True)
                    # No enviar una clave real si los clientes tienen modos divergentes.
                    mailer = build_mailer(dry_run=dry_run) if dry_run is not None else build_mailer()
                    if bool(reset.simulated) != bool(mailer.dry_run):
                        #FPEOPLESUPPORT006-8006: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                        raise IntegrationError('Modos de simulación inconsistentes.')
                    delivery = mailer.send_credentials_email(recipient, str(identity), upn, password)
                    if not delivery.success:
                        audit(identity, user, action, status='PARTIAL', password_reset=True,
                              simulated=bool(reset.simulated))
                        #FPEOPLESUPPORT007-8007: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                        raise IntegrationError('La clave fue regenerada, pero el correo no se entregó. Reintentar el envío genera otra clave.')
                    result = ExternalActionResult('credentials', simulated=reset.simulated or delivery.simulated)
                finally:
                    del password
            elif action == 'sessions':
                result = _confirm(graph.revoke_sign_in_sessions(upn))
            elif action == 'license':
                sku = settings.LUCY_M365_SKU_ID
                if not sku:
                    #FPEOPLESUPPORT008-8008: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                    raise ValidationError('Falta configurar el SKU A1.')
                assigned = graph.get_user_license_ids(upn)
                result = _confirm(graph.toggle_license(upn, sku, assign=sku not in assigned))
                if not result.simulated:
                    _observe_existing_accounts(identity, 'TEAMS', license_assigned=sku not in assigned)
            else:
                result = _confirm(graph.toggle_user_status(upn, enable=False))
                if not result.simulated:
                    _suspend_affiliations(identity, user)
        audit(identity, user, action, status='SUCCEEDED', simulated=bool(result.simulated),
              operation=result.action)
        return result
    #FPEOPLESUPPORT009-8009: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Exception as error:
        audit(identity, user, action, status='FAILED', error_type=type(error).__name__)
        #FPEOPLESUPPORT010-8010: Propaga el fallo original sin convertirlo en éxito.
        raise


@transaction.atomic
def _suspend_affiliations(identity, user):
    Identity.objects.select_for_update().get(pk=identity.pk)
    for affiliation in identity.affiliations.select_for_update().filter(is_active=True, current_status__is_terminal=False):
        affiliation.is_active = False
        affiliation.save(update_fields=['is_active'])
        AffiliationStatusHistory.objects.create(
            affiliation=affiliation, from_status=affiliation.current_status, to_status=affiliation.current_status,
            changed_by=f'operador:{user.get_username()}', changed_by_user=user,
            reason='Suspensión administrativa de identidad: Graph deshabilitado.',
        )


def _moodle_action(identity, action, *, shortname, role_id=5, group_name='', dry_run=None):
    moodle = build_moodle(dry_run=dry_run) if dry_run is not None else build_moodle()
    if dry_run is not None and bool(moodle.dry_run) != dry_run:
        #FPEOPLESUPPORT011-8011: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
        raise IntegrationError('El cliente no respeta el modo de simulación solicitado.')
    upn = identity.institutional_upn
    if moodle.dry_run:
        user_id, course_id = -1, -1
    else:
        remote_user = moodle.get_user_by_username(upn)
        if remote_user is None and action == 'moodle_enrol':
            remote_user = moodle.create_user(upn, identity.first_name, identity.last_name, upn, auth='oidc')
        course = moodle.get_course_by_shortname(shortname)
        if remote_user is None or course is None:
            #FPEOPLESUPPORT012-8012: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('Usuario o curso no encontrado en Moodle.')
        user_id, course_id = remote_user.id, int(course['id'])
    if action == 'moodle_unenrol':
        result = _confirm(moodle.unenrol_user(user_id, course_id, role_id=role_id))
    else:
        result = _confirm(moodle.enrol_user(user_id, course_id, role_id=role_id))
        if not result.simulated:
            _observe_existing_accounts(identity, 'MOODLE', course=shortname, enrolled=True)
        if group_name:
            group_id = moodle.get_or_create_group(course_id, group_name)
            group = _confirm(moodle.add_user_to_group(group_id, user_id, course_id=course_id))
            return ExternalActionResult(action, simulated=result.simulated or group.simulated)
    if action == 'moodle_unenrol' and not result.simulated:
        _observe_existing_accounts(identity, 'MOODLE', course=shortname, enrolled=False)
    return ExternalActionResult(action, simulated=result.simulated)
