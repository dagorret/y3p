"""Celery entry points for dispatch, saga execution and Moodle continuation."""

from celery import current_app, shared_task

from apps.people.models import OutboxEvent
from .dispatcher import dispatch_pending_events
from .orchestrator import run_moodle_step, run_saga as execute_saga
from .sweeper import sweep_stuck_events


@shared_task(name="provisioning.dispatch_outbox", queue="provisioning")
def dispatch_outbox(limit=100):
    """Claim FIFO-eligible outbox rows and publish one task per event."""

    #FTASK01-7001: Dispatcher owns a short claim transaction and never calls providers.
    return dispatch_pending_events(limit=limit)


@shared_task(name="provisioning.run_saga", queue="provisioning")
def run_saga(event_id):
    """Run the synchronous part of one saga on the provisioning queue."""

    #FTASK02-7002: Orchestrator commits each checkpoint and returns before any Moodle wait.
    return execute_saga(event_id)


@shared_task(name="provisioning.moodle_step", queue="moodle", rate_limit="150/m")
def moodle_step(event_id):
    """Run Moodle work on its dedicated, rate-limited worker and continue saga."""

    state = run_moodle_step(event_id)
    if state == OutboxEvent.Status.PENDING:
        #FTASK03-7003: Confirmed Moodle checkpoint releases the next saga pass.
        current_app.send_task(
            "provisioning.dispatch_outbox", queue="provisioning"
        )
    return state


@shared_task(name="provisioning.sweep_stuck_events", queue="provisioning")
def sweep_stuck_events_task():
    """Recover dead workers and lost Moodle continuations on the Beat schedule."""

    #FTASK04-7004: PostgreSQL remains source of truth for retry scheduling.
    return sweep_stuck_events()
