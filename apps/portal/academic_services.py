"""Confirmación atómica PRE → ALUMNO con aislamiento académico por scope.

El ámbito procede de una ruta autorizada. La transacción bloquea identidades y
vinculaciones en orden estable y usa el grafo del dominio: estado, TTL, historial
con operador y evento PROVISION se confirman juntos. El trabajador existente
aprovisiona cuenta, licencia y matrícula según la política y envía la bienvenida
con credenciales mediante MailerClient. Omitir estados ya confirmados conserva
la idempotencia y evita duplicar correos o eventos.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.people.models import Identity, Affiliation
from apps.people.scopes import require_scope_permission, validate_scope
from apps.people.services import transition_affiliation


@transaction.atomic
def confirm_preinscriptions(identity_ids, *, user, scope):
    require_scope_permission(user, scope, 'operate')
    profile_code = validate_scope(scope)
    ids = sorted(set(identity_ids))
    #FACONFIRM01-10101: Se acota el lote antes de bloquear o modificar vinculaciones.
    if scope == 'GRADO' or not 1 <= len(ids) <= 200:
        raise ValidationError('Seleccione entre 1 y 200 alumnos de Posgrado o Extensión.')
    identities = list(Identity.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    affiliations = list(Affiliation.objects.select_for_update(of=('self',)).filter(identity_id__in=ids,
        profile_type__code=profile_code).select_related('current_status').order_by('identity_id', 'pk'))
    #FACONFIRM02-10102: Una selección cruzada revierte todo el lote, aunque incluya alumnos válidos.
    if len(identities) != len(ids) or {aff.identity_id for aff in affiliations} != set(ids):
        raise PermissionDenied('La selección incluye identidades ajenas a este ámbito académico.')
    identity_map = {identity.pk: identity for identity in identities}
    confirmed = skipped = 0
    for affiliation in affiliations:
        if not affiliation.is_active or affiliation.current_status.code != 'PRE':
            skipped += 1
            continue
        identity = identity_map[affiliation.identity_id]
        if not identity.institutional_upn:
            identity.institutional_upn = f'a{identity.identifier}@eco.unrc.edu.ar'
            identity.save(update_fields=['institutional_upn', 'updated_at'])
        #FACONFIRM03-10103: Dominio y Outbox comparten transacción; un fallo intermedio revierte todas las promociones.
        transition_affiliation(affiliation.pk, 'ALUMNO', changed_by=f'operador:{user.get_username()}',
            user=user, expected_from_code='PRE', reason=f'Confirmación masiva de preinscripciones de {scope}.')
        confirmed += 1
    return confirmed, skipped
