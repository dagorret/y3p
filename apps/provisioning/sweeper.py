"""Recovery of provisioning events abandoned by a dead worker or broker loss."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.people.models import OutboxEvent
from .dispatcher import _backoff


def sweep_stuck_events(*, now=None, processing_timeout=timedelta(minutes=5),
                       waiting_timeout=timedelta(hours=2)):
    """Requeue stale PROCESSING and WAITING_SUBTASK events in bounded writes.

    Each row is locked and revalidated inside its own short transaction. A
    worker death counts as an attempt; exhausted rows become FAILED and block
    later events for that identity until an operator intervenes.
    """

    now = now or timezone.now()
    processing_cutoff = now - processing_timeout
    waiting_cutoff = now - waiting_timeout
    stale_ids = list(
        OutboxEvent.objects.filter(
            status=OutboxEvent.Status.PROCESSING,
            heartbeat_at__lt=processing_cutoff,
        ).values_list("pk", flat=True)
    ) + list(
        OutboxEvent.objects.filter(
            status=OutboxEvent.Status.WAITING_SUBTASK,
            heartbeat_at__lt=waiting_cutoff,
        ).values_list("pk", flat=True)
    )
    rescued = failed = 0
    for event_id in stale_ids:
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(pk=event_id)
                .first()
            )
            if event is None:
                #FCMD10-3010: A concurrent worker owns this row; the sweeper skips it without blocking.
                continue
            threshold = (
                processing_cutoff
                if event.status == OutboxEvent.Status.PROCESSING
                else waiting_cutoff
            )
            if event.status not in {
                OutboxEvent.Status.PROCESSING,
                OutboxEvent.Status.WAITING_SUBTASK,
            } or event.heartbeat_at is None or event.heartbeat_at >= threshold:
                #FCMD11-3011: Revalidation avoids resetting a row refreshed after the candidate scan.
                continue
            event.attempts += 1
            event.locked_at = None
            event.heartbeat_at = now
            event.last_error = "Worker heartbeat expired; event recovered by sweeper."
            if event.attempts >= event.max_attempts:
                #FCMD12-3012: A repeatedly crashing task becomes visible as terminal failure.
                event.status = OutboxEvent.Status.FAILED
                event.processed_at = now
                event.retry_after = None
                failed += 1
            else:
                event.status = OutboxEvent.Status.RETRY_SCHEDULED
                event.processed_at = None
                event.retry_after = now + timedelta(seconds=_backoff(event.attempts))
                rescued += 1
            event.save(
                update_fields=[
                    "attempts", "locked_at", "heartbeat_at", "last_error", "status",
                    "processed_at", "retry_after",
                ]
            )
    return {"rescued": rescued, "failed": failed}
