"""Reclamación transaccional breve de solicitudes del Outbox de personas."""

import random
from datetime import timedelta

from celery import current_app
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.people.models import OutboxEvent


def claim_events(limit=100):
    """Reclama eventos elegibles sin mantener bloqueos durante operaciones de red.

    El patrón de reclamación usa NOT EXISTS para respetar el orden por
    identidad y select_for_update(skip_locked=True) para que trabajadores
    concurrentes tomen filas diferentes sin esperarse. La transacción confirma
    PROCESSING y las marcas temporales antes de devolver los identificadores.

    Esta separación reduce la contención, pero admite que un proceso muera
    después de reclamar y antes de publicar: el barrido de actividad vencida
    recupera esos eventos. No se presume una transacción distribuida con Celery.
    """

    now = timezone.now()
    blockers = OutboxEvent.objects.filter(
        identity_id=OuterRef("identity_id"),
        id__lt=OuterRef("id"),
    ).exclude(status__in=[OutboxEvent.Status.SUCCEEDED, OutboxEvent.Status.SIMULATED])
    ready = Q(status=OutboxEvent.Status.PENDING) | Q(
        status=OutboxEvent.Status.RETRY_SCHEDULED,
        retry_after__lte=now,
    )
    claimed_ids = []
    with transaction.atomic():
        #FCMD01-3001: Respeta el orden por identidad y solo toma reintentos cuyo plazo se cumplió.
        events = list(
            OutboxEvent.objects.filter(ready)
            .filter(~Exists(blockers))
            .order_by("id")
            .select_for_update(skip_locked=True)[:limit]
        )
        for event in events:
            #FCMD02-3002: El bloqueo protege el cambio de estado hasta confirmar esta transacción breve.
            event.status = OutboxEvent.Status.PROCESSING
            event.locked_at = now
            event.heartbeat_at = now
            event.retry_after = None
            claimed_ids.append(event.pk)
        if events:
            OutboxEvent.objects.bulk_update(
                events, ["status", "locked_at", "heartbeat_at", "retry_after"]
            )
    return claimed_ids


def dispatch_pending_events(limit=100):
    """Reclama un lote y publica tareas después de liberar los bloqueos de la base.

    La publicación ocurre fuera de la transacción. Si falla el intermediario
    de mensajes, cada evento vuelve a un estado de reintento persistente;
    PostgreSQL sigue siendo la fuente de verdad, no la memoria del trabajador.
    """

    event_ids = claim_events(limit=limit)
    published = 0
    for event_id in event_ids:
        try:
            #FCMD03-3003: La publicación sigue a la confirmación y liberación del bloqueo.
            current_app.send_task(
                "provisioning.run_saga", args=[event_id], queue="provisioning"
            )
            published += 1
        #FPROVISIONINGDISPATCHER001-8001: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except Exception as error:
            #FCMD04-3004: Un fallo del intermediario deja el reintento registrado en la base.
            _reschedule_after_publish_error(event_id, error)
    return {"claimed": len(event_ids), "published": published}


def _reschedule_after_publish_error(event_id, error):
    """Reprograma atómicamente una reclamación que no pudo publicarse, o registra su fallo final."""

    now = timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        event.attempts += 1
        event.last_error = f"{error.__class__.__name__}: falló el despacho a Celery."
        if event.attempts >= event.max_attempts:
            #FCMD05-3005: Agotar intentos conserva un fallo visible que retiene las órdenes posteriores.
            event.status = OutboxEvent.Status.FAILED
            event.processed_at = now
            event.retry_after = None
        else:
            event.status = OutboxEvent.Status.RETRY_SCHEDULED
            event.processed_at = None
            event.retry_after = now + timedelta(seconds=_backoff(event.attempts))
        event.save(
            update_fields=["attempts", "last_error", "status", "processed_at", "retry_after"]
        )


def _backoff(attempts):
    """Calcula una espera exponencial acotada con dispersión aleatoria."""

    return min(2**attempts * 30, 3600) + random.randint(0, 30)
