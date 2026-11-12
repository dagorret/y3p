"""Barrido periódico acotado de TTL con revalidación e idempotencia del dominio.

Las vinculaciones sin regla is_expiration se excluyen mediante EXISTS y se
reportan sin ocupar el lote. Cada candidata usa una transacción breve con
skip_locked: una fila ocupada no detiene el barrido. El dominio confirma estado,
historial y suspensión Outbox juntos; los proveedores se ejecutan después.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import Affiliation, SagaTransitionRule
from .services import expire_affiliation, TransitionError


def process_expired_affiliations(*, limit=500):
    #FTTLSWEEP01-10301: Cada ejecución procesa como máximo un lote finito.
    if not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ValidationError('El lote TTL debe contener entre 1 y 2000 vinculaciones.')
    rules = SagaTransitionRule.objects.filter(profile_type_id=OuterRef('profile_type_id'),
        from_status_id=OuterRef('current_status_id'), is_expiration=True)
    expired = Affiliation.objects.filter(is_active=True, expires_at__lte=timezone.now())
    result = {'expired': 0, 'errors': 0, 'skipped': 0, 'orphans': expired.filter(~Exists(rules)).count()}
    ids = list(expired.filter(Exists(rules)).order_by('expires_at', 'pk').values_list('pk', flat=True)[:limit])
    for affiliation_id in ids:
        try:
            with transaction.atomic():
                locked = Affiliation.objects.select_for_update(skip_locked=True).filter(pk=affiliation_id).first()
                if locked is None:
                    #FTTLSWEEP02-10302: Una fila ocupada o retirada no bloquea las restantes.
                    result['skipped'] += 1
                    continue
                if expire_affiliation(affiliation_id, changed_by='tarea:ttl_periodico'):
                    #FTTLSWEEP03-10303: Cada cambio masivo conserva la transición individual auditada del dominio.
                    result['expired'] += 1
                else:
                    result['skipped'] += 1
        except TransitionError:
            #FTTLSWEEP04-10304: Una anomalía de configuración revierte solo su vinculación.
            result['errors'] += 1
    return result
