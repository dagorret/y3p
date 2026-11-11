"""Emisión atómica de órdenes masivas sin ejecutar APIs durante el POST."""

import uuid

from celery import current_app
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch

from apps.people.models import Affiliation, Identity, OutboxEvent
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY
from apps.people.support import audit, require_permission


@transaction.atomic
def enqueue_student_actions(identity_ids, action, *, user, options=None):
    """Guarda un lote completo de hasta 50 solicitudes o revierte todas sus filas.

    La selección se valida en el servidor y cada evento conserva actor y lote,
    nunca contraseñas. El aviso a Celery ocurre después de confirmar la base.
    Reutilizar Outbox mantiene el orden por identidad y sus reintentos; el
    trabajador vuelve a validar permisos para respetar revocaciones posteriores
    a la selección. La interfaz confirma registro, no ejecución remota inmediata.
    """
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL if action == 'license' else OPERATE_STUDENT_DAILY)
    if action not in ('credentials', 'moodle_enrol', 'license'):
        #FPORTALBULKSERVICES001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Acción masiva no permitida.')
    ids = list(dict.fromkeys(identity_ids))
    if not ids or len(ids) > 50:
        #FPORTALBULKSERVICES002-8002: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Seleccione entre 1 y 50 alumnos de la página.')
    if action == 'moodle_enrol':
        from .forms import MoodleExpressForm
        form = MoodleExpressForm(options or {})
        if not form.is_valid():
            #FPORTALBULKSERVICES003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('Indique un curso Moodle y un rol válido.')
        options = form.cleaned_data
    else:
        options = {}
    identities = list(Identity.objects.select_for_update().filter(pk__in=ids).order_by('pk').prefetch_related(
        Prefetch('affiliations', queryset=Affiliation.objects.filter(profile_type__code='ALUMNO').order_by('-is_active', '-pk'),
                 to_attr='student_affiliations'),
    ))
    if len(identities) != len(ids) or any(not identity.student_affiliations for identity in identities):
        #FPORTALBULKSERVICES004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('La selección contiene identidades que no son alumnos.')
    batch_id = str(uuid.uuid4())
    events = []
    for identity in identities:
        event = OutboxEvent.objects.create(
            identity=identity, affiliation=identity.student_affiliations[0], requested_by=user, action='SUPPORT',
            payload={'support_action': action, 'support_options': options, 'batch_id': batch_id},
        )
        audit(identity, user, 'bulk_queued', status='PENDING', operation=action,
              batch_id=batch_id, event_id=event.pk)
        events.append(event)
    transaction.on_commit(lambda: current_app.send_task('provisioning.dispatch_outbox', queue='provisioning'), robust=True)
    return batch_id, events
