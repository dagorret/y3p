"""Domain saga runner for durable Graph, Moodle and SMTP provisioning steps."""

import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.exceptions import (
    AuthenticationError,
    ConfigurationError,
    IntegrationError,
    OperationForbiddenError,
    RateLimitExceededError,
    RemoteServiceUnavailableError,
    ResourceNotFoundError,
)
from apps.integrations.services.graph import MicrosoftGraphClient
from apps.integrations.services.mailer import MailerClient
from apps.integrations.services.moodle import MoodleRestClient
from apps.people.models import Affiliation, OutboxEvent


class PermanentProvisioningError(Exception):
    """Nonretryable local or provider result that needs operator correction."""


def run_saga(event_id, *, graph=None, moodle=None, mailer=None, dry_run=None, enqueue_moodle=None):
    """Execute one event up to its Moodle continuation or terminal state.

    Provider calls happen outside database transactions. Every confirmed action
    is then stored through a short locked checkpoint write, so a retry resumes
    after the last committed step. The password-and-mail stage is atomic at the
    orchestration level: every retry generates a fresh ephemeral secret and
    stores only the result metadata.
    """

    simulated = settings.LUCY_DRY_RUN if dry_run is None else bool(dry_run)
    graph = graph or _graph_client(simulated)
    moodle = moodle or _moodle_client(simulated)
    mailer = mailer or _mailer_client(simulated)
    event = _load_event(event_id)
    if event.status != OutboxEvent.Status.PROCESSING:
        #FSRV25-6025: Duplicate or stale task messages cannot run without a committed dispatcher claim.
        return event.status
    try:
        if event.action == "PROVISION":
            state = _run_provision(event_id, event, graph, moodle, mailer, simulated, enqueue_moodle)
        elif event.action in {"SUSPEND", "TERMINATE"}:
            state = _run_suspend(event_id, event, graph, moodle, simulated, enqueue_moodle)
        else:
            #FSRV01-6001: Unknown intent is permanent configuration error, never silently ignored.
            raise PermanentProvisioningError(f"Acción de outbox no soportada: {event.action}.")
        return state
    except Exception as error:
        #FSRV02-6002: Every exception leaves PROCESSING through durable retry or FAILED state.
        return record_failure(event_id, error)


def _run_provision(event_id, event, graph, moodle, mailer, simulated, enqueue_moodle):
    """Provision Graph, license, Moodle continuation, then credentials email."""

    payload = event.payload
    upn = payload.get("upn", "")
    if not upn:
        #FSRV03-6003: An institutionally assigned UPN is required before external account mutation.
        raise PermanentProvisioningError("La identidad no tiene institutional_upn configurado.")

    if not _checkpoint_complete(event_id, "graph_user"):
        existing = graph.get_user(upn)
        if existing is not None:
            graph_result = existing
        else:
            #FSRV04-6004: The bootstrap password is discarded and never written to checkpoints.
            bootstrap_password = _temporary_password()
            try:
                graph_result = graph.create_user(
                    payload["first_name"],
                    payload["last_name"],
                    upn,
                    bootstrap_password,
                    force_change_password=True,
                )
            finally:
                del bootstrap_password
        _save_checkpoint(
            event_id,
            "graph_user",
            "CONFIRMED",
            simulated=simulated or bool(getattr(graph_result, "simulated", False)),
            details={"created": bool(graph_result.created)},
        )

    graph_state = _checkpoint_value(event_id, "graph_user")
    requires_license = bool(payload.get("requires_m365_license", False))
    if requires_license and not _checkpoint_complete(event_id, "license"):
        sku_id = settings.LUCY_M365_SKU_ID
        if not sku_id:
            #FSRV05-6005: Missing SKU is permanent configuration failure, no remote write is attempted.
            raise ConfigurationError("Falta configurar M365_A1_SKU_ID.")
        result = graph.assign_license(upn, sku_id)
        _save_checkpoint(
            event_id,
            "license",
            "CONFIRMED",
            simulated=simulated or bool(getattr(result, "simulated", False)),
            details={"assigned": True},
        )
    elif not requires_license and not _checkpoint_complete(event_id, "license"):
        _save_checkpoint(event_id, "license", "SKIPPED_NOT_REQUIRED", simulated=simulated)

    if not _checkpoint_complete(event_id, "moodle"):
        _mark_waiting_subtask(event_id)
        if enqueue_moodle:
            #FSRV06-6006: Moodle continues on its isolated queue; this worker never waits on a result.
            enqueue_moodle(event_id)
        else:
            from celery import current_app

            current_app.send_task("provisioning.moodle_step", args=[event_id], queue="moodle")
        return OutboxEvent.Status.WAITING_SUBTASK

    if not _checkpoint_complete(event_id, "password_and_mail"):
        if not graph_state.get("details", {}).get("created", False):
            #FSRV07-6007: Existing accounts are never reset or sent a new password.
            _save_checkpoint(event_id, "password_and_mail", "SKIPPED_EXISTING_USER", simulated=simulated)
        else:
            _send_credentials(event_id, graph, mailer, payload, simulated)

    #FSRV08-6008: A dry-run is terminal but distinct from a remotely completed event.
    return _mark_terminal(event_id, simulated=simulated)


def _run_suspend(event_id, event, graph, moodle, simulated, enqueue_moodle):
    """Remove this affiliation's Moodle access and preserve shared M365 access."""

    if not _checkpoint_complete(event_id, "moodle"):
        _mark_waiting_subtask(event_id)
        if enqueue_moodle:
            #FSRV09-6009: Suspension Moodle work follows the same asynchronous continuation.
            enqueue_moodle(event_id)
        else:
            from celery import current_app

            current_app.send_task("provisioning.moodle_step", args=[event_id], queue="moodle")
        return OutboxEvent.Status.WAITING_SUBTASK

    #FSRV10-6010: License and account decisions use live active affiliations, excluding this one.
    other_active = Affiliation.objects.filter(
        identity_id=event.identity_id, is_active=True
    ).exclude(pk=event.affiliation_id).select_related("current_status")
    still_required = other_active.filter(current_status__requires_m365_license=True).exists()
    account_still_active = other_active.exists()
    upn = event.payload.get("upn", "")
    sku_id = settings.LUCY_M365_SKU_ID

    if still_required:
        if not _checkpoint_complete(event_id, "license"):
            #FSRV11-6011: Another active affiliation still requires the shared license.
            _save_checkpoint(event_id, "license", "SKIPPED_STILL_REQUIRED", simulated=simulated)
    elif not _checkpoint_complete(event_id, "license"):
        if not sku_id:
            #FSRV12-6012: Missing SKU blocks a needed license removal.
            raise ConfigurationError("Falta configurar M365_A1_SKU_ID.")
        result = graph.remove_license(upn, sku_id)
        _save_checkpoint(
            event_id, "license", "CONFIRMED",
            simulated=simulated or bool(getattr(result, "simulated", False)),
            details={"assigned": False},
        )

    if account_still_active:
        if not _checkpoint_complete(event_id, "graph_disable"):
            #FSRV13-6013: Other active affiliations keep the shared Entra account enabled.
            _save_checkpoint(event_id, "graph_disable", "SKIPPED_STILL_ACTIVE", simulated=simulated)
    elif not _checkpoint_complete(event_id, "graph_disable"):
        result = graph.disable_user(upn)
        _save_checkpoint(
            event_id, "graph_disable", "CONFIRMED",
            simulated=simulated or bool(getattr(result, "simulated", False)),
            details={"enabled": False},
        )

    #FSRV14-6014: TERMINATE follows suspend until the institution approves remote deletion.
    if event.action == "TERMINATE" and not _checkpoint_complete(event_id, "graph_delete"):
        _save_checkpoint(event_id, "graph_delete", "SKIPPED_POLICY", simulated=simulated)
    return _mark_terminal(event_id, simulated=simulated)


def run_moodle_step(event_id, *, moodle=None, dry_run=None):
    """Execute one Moodle continuation outside a DB transaction and checkpoint it.

    The event remains WAITING_SUBTASK while network calls run. On success the
    checkpoint and PENDING continuation state commit together; the task caller
    may then re-enqueue the dispatcher. Failures are returned to durable retry
    handling without leaving the event waiting indefinitely.
    """

    event = _load_event(event_id)
    if event.status != OutboxEvent.Status.WAITING_SUBTASK:
        #FSRV15-6015: A stale duplicate continuation cannot execute after state has moved.
        return event.status
    simulated = settings.LUCY_DRY_RUN if dry_run is None else bool(dry_run)
    moodle = moodle or _moodle_client(simulated)
    try:
        outcome = _perform_moodle(event, moodle)
        #FSRV16-6016: Checkpoint and requeue state are one short transaction after remote success.
        with transaction.atomic():
            locked = OutboxEvent.objects.select_for_update().get(pk=event_id)
            if locked.status != OutboxEvent.Status.WAITING_SUBTASK:
                #FSRV26-6026: A task that lost the race with the sweeper cannot overwrite its recovery state.
                return locked.status
            results = dict(locked.step_results)
            results["moodle"] = {
                "status": outcome["status"],
                "simulated": simulated or outcome.get("simulated", False),
                "details": outcome.get("details", {}),
            }
            locked.step_results = results
            locked.status = OutboxEvent.Status.PENDING
            locked.locked_at = None
            locked.heartbeat_at = timezone.now()
            locked.last_error = ""
            locked.save(
                update_fields=["step_results", "status", "locked_at", "heartbeat_at", "last_error"]
            )
        return OutboxEvent.Status.PENDING
    except Exception as error:
        #FSRV17-6017: Moodle failures use the same bounded durable retry policy as the main saga.
        return record_failure(event_id, error, checkpoint="moodle")


def _perform_moodle(event, moodle):
    """Converge Moodle user, course enrollment and optional commission group."""

    payload = event.payload
    upn = payload.get("upn", "")
    shortname = payload.get("course_shortname", "")
    if event.action == "PROVISION":
        user = moodle.create_user(
            username=upn,
            first_name=payload["first_name"],
            last_name=payload["last_name"],
            email=upn,
            auth="oidc",
        )
        if not shortname:
            return {"status": "SKIPPED_NO_COURSE", "simulated": user.simulated, "details": {}}
        course = moodle.get_course_by_shortname(shortname)
        if course is None:
            #FSRV18-6018: Missing course is a typed configuration/resource error, not a false success.
            raise ResourceNotFoundError(f"No existe el curso Moodle {shortname}.")
        enrollment = moodle.enrol_user(user.id, int(course["id"]), role_id=5)
        group_name = payload.get("group_name")
        simulated = user.simulated or enrollment.simulated
        if group_name:
            group_id = moodle.get_or_create_group(int(course["id"]), group_name)
            group = moodle.add_user_to_group(group_id, user.id, course_id=int(course["id"]))
            simulated = simulated or group.simulated
        return {
            "status": "CONFIRMED",
            "simulated": simulated,
            "details": {"enrolled": enrollment.success},
        }

    user = moodle.get_user_by_username(upn)
    if user is None:
        return {"status": "SKIPPED_USER_MISSING", "simulated": False, "details": {}}
    if not shortname:
        return {"status": "SKIPPED_NO_COURSE", "simulated": user.simulated, "details": {}}
    course = moodle.get_course_by_shortname(shortname)
    if course is None:
        return {"status": "SKIPPED_COURSE_MISSING", "simulated": user.simulated, "details": {}}
    result = moodle.unenrol_user(user.id, int(course["id"]), role_id=5)
    return {
        "status": "CONFIRMED",
        "simulated": user.simulated or result.simulated,
        "details": {"unenrolled": result.success},
    }


def _send_credentials(event_id, graph, mailer, payload, simulated):
    """Rotate the credential and deliver it in one checkpointable ephemeral step."""

    password = _temporary_password()
    try:
        password_result = graph.set_temporary_password(
            user_identifier=payload["upn"], password=password
        )
        delivery = mailer.send_credentials_email(
            recipient=payload.get("recipient", ""),
            full_name=payload["full_name"],
            upn=payload["upn"],
            password=password,
        )
        if not delivery.success:
            error_type = (delivery.error_message or "DeliveryError").split(":", 1)[0]
            if error_type in {"ValueError", "ConfigurationError", "InvalidRecipientError"}:
                #FSRV19-6019: Invalid local mail configuration is permanent until operator correction.
                raise PermanentProvisioningError(f"Entrega de credenciales inválida ({error_type}).")
            #FSRV20-6020: Transient SMTP failures retry and generate a different secret next attempt.
            raise RemoteServiceUnavailableError(f"Entrega SMTP fallida ({error_type}).")
        _save_checkpoint(
            event_id,
            "password_and_mail",
            "CONFIRMED",
            simulated=simulated
            or bool(getattr(password_result, "simulated", False))
            or delivery.simulated,
            details={"sent": True},
        )
    finally:
        #FSRV21-6021: The ephemeral secret is dropped even if Graph or SMTP raises.
        del password


def record_failure(event_id, error, *, checkpoint=None):
    """Persist retry or terminal failure without recording remote secrets."""

    now = timezone.now()
    permanent = isinstance(
        error,
        (
            PermanentProvisioningError,
            AuthenticationError,
            OperationForbiddenError,
            ConfigurationError,
            ResourceNotFoundError,
        ),
    )
    is_rate_limit = isinstance(error, RateLimitExceededError)
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if not permanent and not is_rate_limit:
            event.attempts += 1
        exhausted = not is_rate_limit and event.attempts >= event.max_attempts
        if permanent or exhausted:
            #FSRV22-6022: Nonretryable or exhausted events become terminal and continue blocking FIFO.
            event.status = OutboxEvent.Status.FAILED
            event.processed_at = now
            event.retry_after = None
        else:
            event.status = OutboxEvent.Status.RETRY_SCHEDULED
            event.processed_at = None
            delay = error.retry_after if is_rate_limit and error.retry_after else _backoff(event.attempts)
            event.retry_after = now + timedelta(seconds=max(1, int(delay)))
        event.locked_at = None
        event.heartbeat_at = now
        event.last_error = f"{error.__class__.__name__}: provisioning step failed."
        if checkpoint:
            results = dict(event.step_results)
            results[checkpoint] = {
                "status": "FAILED",
                "simulated": False,
                "error_type": error.__class__.__name__,
            }
            event.step_results = results
        event.save(
            update_fields=[
                "attempts", "status", "processed_at", "retry_after", "locked_at",
                "heartbeat_at", "last_error", "step_results",
            ]
        )
        return event.status


def _save_checkpoint(event_id, name, status, *, simulated=False, details=None):
    """Merge one confirmed step into freshly locked JSON checkpoint data."""

    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        #FSRV23-6023: Each checkpoint merges the latest JSON, never a stale worker snapshot.
        results = dict(event.step_results)
        results[name] = {"status": status, "simulated": bool(simulated), "details": details or {}}
        event.step_results = results
        event.heartbeat_at = timezone.now()
        event.last_error = ""
        event.save(update_fields=["step_results", "heartbeat_at", "last_error"])


def _checkpoint_complete(event_id, name):
    """Return whether a step is already confirmed or explicitly skipped."""

    value = _checkpoint_value(event_id, name)
    status = value.get("status", "")
    return status == "CONFIRMED" or status.startswith("SKIPPED_")


def _checkpoint_value(event_id, name):
    """Read one checkpoint from the current durable event row."""

    event = OutboxEvent.objects.only("step_results").get(pk=event_id)
    return event.step_results.get(name, {})


def _mark_waiting_subtask(event_id):
    """Commit WAITING_SUBTASK before a task is published to the Moodle queue."""

    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        #FSRV24-6024: Durable waiting state precedes broker submission and is recoverable by sweeper.
        event.status = OutboxEvent.Status.WAITING_SUBTASK
        event.locked_at = None
        event.heartbeat_at = timezone.now()
        event.save(update_fields=["status", "locked_at", "heartbeat_at"])


def _mark_terminal(event_id, *, simulated):
    """Finish event and timestamp in one final transaction."""

    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        event.status = (
            OutboxEvent.Status.SIMULATED if simulated else OutboxEvent.Status.SUCCEEDED
        )
        event.processed_at = timezone.now()
        event.locked_at = None
        event.heartbeat_at = event.processed_at
        event.retry_after = None
        event.save(
            update_fields=["status", "processed_at", "locked_at", "heartbeat_at", "retry_after"]
        )
        return event.status


def _load_event(event_id):
    """Read event context without retaining a database lock during provider I/O."""

    return OutboxEvent.objects.select_related(
        "identity", "affiliation", "affiliation__current_status"
    ).get(pk=event_id)


def _temporary_password(length=32):
    """Generate an ephemeral policy-safe password with letters, digits and symbols."""

    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _backoff(attempts):
    """Return bounded exponential retry delay with jitter."""

    import random

    return min(2**attempts * 30, 3600) + random.randint(0, 30)


def _graph_client(dry_run):
    """Build a Graph adapter from settings without performing network I/O."""

    return MicrosoftGraphClient(
        tenant_id=settings.LUCY_M365_TENANT_ID,
        client_id=settings.LUCY_M365_CLIENT_ID,
        client_secret=settings.LUCY_M365_CLIENT_SECRET,
        dry_run=dry_run,
    )


def _moodle_client(dry_run):
    """Build a Moodle adapter from settings without performing network I/O."""

    return MoodleRestClient(
        base_url=settings.LUCY_MOODLE_BASE_URL,
        token=settings.LUCY_MOODLE_TOKEN,
        dry_run=dry_run,
    )


def _mailer_client(dry_run):
    """Build a secure SMTP adapter from environment-backed Django settings."""

    return MailerClient(
        host=settings.LUCY_SMTP_HOST,
        port=settings.LUCY_SMTP_PORT,
        username=settings.LUCY_SMTP_USERNAME,
        password=settings.LUCY_SMTP_PASSWORD,
        from_email=settings.LUCY_SMTP_FROM_EMAIL,
        dry_run=dry_run,
    )
