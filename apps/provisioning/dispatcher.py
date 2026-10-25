"""Short-transaction claims for the people transactional outbox."""

import random
from datetime import timedelta

from celery import current_app
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.people.models import OutboxEvent


def claim_events(limit=100):
    """Claim dispatchable events without holding locks during broker or API I/O.

    The ``NOT EXISTS`` condition makes every earlier nonterminal event block
    later rows for its identity. ``skip_locked`` permits concurrent dispatcher
    workers to claim different identities. The atomic block commits PROCESSING
    and timestamps before this function returns the IDs to its caller.
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
        #FCMD01-3001: Claim eligibility is FIFO and only retry rows past due are ready.
        events = list(
            OutboxEvent.objects.filter(ready)
            .filter(~Exists(blockers))
            .order_by("id")
            .select_for_update(skip_locked=True)[:limit]
        )
        for event in events:
            #FCMD02-3002: Claim lock protects the state transition only until this transaction commits.
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
    """Claim a batch and publish saga task IDs after claim locks have committed.

    Publishing happens outside the database transaction. If the broker is
    unavailable, each affected event is moved back to retry state so the durable
    database remains the source of truth rather than relying on Celery retries.
    """

    event_ids = claim_events(limit=limit)
    published = 0
    for event_id in event_ids:
        try:
            #FCMD03-3003: Broker dispatch follows the committed short claim transaction.
            current_app.send_task(
                "provisioning.run_saga", args=[event_id], queue="provisioning"
            )
            published += 1
        except Exception as error:
            #FCMD04-3004: Broker failure releases the claim through durable retry metadata.
            _reschedule_after_publish_error(event_id, error)
    return {"claimed": len(event_ids), "published": published}


def _reschedule_after_publish_error(event_id, error):
    """Return an unpublished claim to retry or terminal failure atomically."""

    now = timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        event.attempts += 1
        event.last_error = f"{error.__class__.__name__}: Celery dispatch failed."
        if event.attempts >= event.max_attempts:
            #FCMD05-3005: Exhausted broker failures stay visible and block FIFO for operator review.
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
    """Return capped exponential delay with jitter in seconds."""

    return min(2**attempts * 30, 3600) + random.randint(0, 30)
