"""Outbox transaccional: persiste el lote y su auditoría antes de avisar a Celery.

El bloqueo de identidades serializa órdenes concurrentes; las vinculaciones se
precargan para evitar N+1. Ninguna llamada remota participa del POST.
"""

import uuid

from celery import current_app
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Prefetch

from apps.people.scopes import require_scope_permission, validate_scope
from apps.people.models import Affiliation, Identity, OutboxEvent
from apps.people.support import audit


@transaction.atomic
def enqueue_student_actions(identity_ids, action, *, user, options=None, scope='GRADO'):
    """Guarda un lote completo de hasta 200 solicitudes o revierte todas sus filas.

    La selección se valida en el servidor y cada evento conserva actor y lote,
    nunca contraseñas. El aviso a Celery ocurre después de confirmar la base.
    Reutilizar Outbox mantiene el orden por identidad y sus reintentos; el
    trabajador vuelve a validar permisos para respetar revocaciones posteriores
    a la selección. La interfaz confirma registro, no ejecución remota inmediata.
    """
    require_scope_permission(user, scope, 'admin' if action == 'license' else 'operate')
    profile_code = validate_scope(scope)
    #FBULKSCOPE01-10104: Los nuevos ámbitos exponen solo notificaciones y confirmación por su servicio específico.
    if scope != 'GRADO' and action != 'notification':
        raise ValidationError('Acción no disponible en este ámbito académico.')
    if action not in ('credentials', 'moodle_enrol', 'license', 'notification'):
        #FPORTALBULKSERVICES001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Acción masiva no permitida.')
    ids = list(dict.fromkeys(identity_ids))
    if not ids or len(ids) > 200:
        #FPORTALBULKSERVICES002-8002: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Seleccione entre 1 y 200 alumnos de la página.')
    if action == 'moodle_enrol':
        from .forms import MoodleExpressForm
        form = MoodleExpressForm(options or {})
        if not form.is_valid():
            #FPORTALBULKSERVICES003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('Indique un curso Moodle y un rol válido.')
        options = form.cleaned_data
    elif action == 'notification':
        from apps.people.notifications import validate_notification
        options = validate_notification(options or {})
    else:
        options = {}
    identities = list(Identity.objects.select_for_update().filter(pk__in=ids).order_by('pk').prefetch_related(
        Prefetch('affiliations', queryset=Affiliation.objects.filter(profile_type__code=profile_code).order_by('-is_active', '-pk'),
                 to_attr='student_affiliations'),
    ))
    if len(identities) != len(ids) or any(not identity.student_affiliations for identity in identities):
        if scope != 'GRADO' or Affiliation.objects.filter(identity_id__in=ids).exclude(profile_type__code=profile_code).exists():
            #FBULKSCOPE02-10105: La selección ajena se rechaza con 403 sin crear eventos parciales.
            raise PermissionDenied('La selección incluye identidades ajenas a este ámbito académico.')
        #FPORTALBULKSERVICES004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('La selección contiene identidades que no son alumnos.')
    batch_id = str(uuid.uuid4())
    events = []
    for identity in identities:
        event_options = options
        if action == 'notification':
            from apps.people.notifications import render_notification
            event_options = render_notification(identity, options)
        event = OutboxEvent.objects.create(
            identity=identity, affiliation=identity.student_affiliations[0], requested_by=user, action='SUPPORT',
            payload={'scope': scope, 'support_action': action, 'support_options': event_options, 'batch_id': batch_id},
        )
        audit(identity, user, 'bulk_queued', status='PENDING', operation=action,
              batch_id=batch_id, event_id=event.pk)
        events.append(event)
    transaction.on_commit(lambda: current_app.send_task('provisioning.dispatch_outbox', queue='provisioning'), robust=True)
    return batch_id, events
