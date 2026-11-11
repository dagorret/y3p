"""Entradas Celery para despacho, orquestación y continuación Moodle."""

from celery import current_app, shared_task

from apps.people.models import OutboxEvent
from .dispatcher import dispatch_pending_events
from .orchestrator import run_moodle_step, run_saga as execute_saga
from .sweeper import sweep_stuck_events


@shared_task(name="provisioning.dispatch_outbox", queue="provisioning")
def dispatch_outbox(limit=100):
    """Reclama eventos elegibles en orden y publica una tarea por evento."""

    #FTASK01-7001: El despachador reclama en una transacción breve y no invoca proveedores.
    return dispatch_pending_events(limit=limit)


@shared_task(name="provisioning.run_saga", queue="provisioning")
def run_saga(event_id):
    """Ejecuta la parte sincrónica de una saga en la cola de aprovisionamiento."""

    #FTASK02-7002: La orquestación confirma avances antes de liberar la continuación Moodle.
    return execute_saga(event_id)


@shared_task(name="provisioning.moodle_step", queue="moodle", rate_limit="150/m")
def moodle_step(event_id):
    """Ejecuta Moodle en su cola independiente, con límite de llamadas y continuación de la saga."""

    state = run_moodle_step(event_id)
    if state == OutboxEvent.Status.PENDING:
        #FTASK03-7003: Un punto Moodle confirmado permite el siguiente despacho de la saga.
        current_app.send_task(
            "provisioning.dispatch_outbox", queue="provisioning"
        )
    return state


@shared_task(name="provisioning.sweep_stuck_events", queue="provisioning")
def sweep_stuck_events_task():
    """Recupera trabajadores perdidos y continuaciones Moodle vencidas desde la agenda periódica."""

    #FTASK04-7004: PostgreSQL conserva la autoridad sobre los plazos de reintento.
    return sweep_stuck_events()
