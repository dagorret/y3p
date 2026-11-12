"""Orquestador de estados y resultados persistentes de Graph, Moodle y SMTP."""

import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
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
from apps.integrations.models import AdmissionCourseRule
from apps.people.models import Affiliation, OutboxEvent


#FPROVISIONINGORCHESTRATOR001-8001: Declara la excepción de dominio PermanentProvisioningError para distinguir esta regla.
class PermanentProvisioningError(Exception):
    """Fallo no reintentable que requiere una corrección del operador o de configuración."""


def run_saga(event_id, *, graph=None, moodle=None, mailer=None, dry_run=None, enqueue_moodle=None):
    """Ejecuta un evento hasta su continuación Moodle o su estado final.

    Las llamadas remotas ocurren fuera de las transacciones. Cada paso
    confirmado registra un punto de control en una escritura breve y bloqueada;
    un reintento continúa desde ese punto. La etapa de contraseña y correo
    regenera un secreto efímero en cada intento y conserva solo sus metadatos.

    Se acepta consistencia eventual entre proveedores para evitar bloqueos
    largos de PostgreSQL. Las operaciones remotas deben tolerar repeticiones:
    una caída puede ocurrir después de la respuesta y antes del registro local.
    """

    simulated = settings.LUCY_DRY_RUN if dry_run is None else bool(dry_run)
    graph = graph or _graph_client(simulated)
    moodle = moodle or _moodle_client(simulated)
    mailer = mailer or _mailer_client(simulated)
    event = _load_event(event_id)
    if event.status != OutboxEvent.Status.PROCESSING:
        #FSRV25-6025: Una tarea duplicada no se ejecuta sin una reclamación PROCESSING confirmada.
        return event.status
    try:
        if event.action == 'SUPPORT':
            return _run_support(event, simulated)
        elif event.action == "PROVISION":
            state = _run_provision(event_id, event, graph, moodle, mailer, simulated, enqueue_moodle)
        elif event.action in {"SUSPEND", "TERMINATE"}:
            state = _run_suspend(event_id, event, graph, moodle, simulated, enqueue_moodle)
        else:
            #FSRV01-6001: Una acción desconocida es un error permanente de configuración.
            raise PermanentProvisioningError(f"Acción de outbox no soportada: {event.action}.")
        return state
    #FPROVISIONINGORCHESTRATOR003-8003: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Exception as error:
        #FSRV02-6002: Todo fallo abandona PROCESSING mediante reintento persistido o estado final.
        return record_failure(event_id, error)


def _run_support(event, simulated):
    """Reutiliza FIFO, recuperación y checkpoints para las órdenes de ventanilla."""
    from apps.people.support import perform_support
    operation = event.payload.get('support_action')
    if event.requested_by is None or operation not in ('credentials', 'moodle_enrol', 'license', 'notification'):
        #FPROVISIONINGORCHESTRATOR004-8004: Origina PermanentProvisioningError; el llamador conserva la causa y decide su tratamiento.
        raise PermanentProvisioningError('La solicitud administrativa no es válida.')
    from apps.people.scopes import SCOPES
    scope = event.payload.get('scope', 'GRADO')
    #FSCOPEDWORKER01-10604: El ámbito de una orden SUPPORT debe coincidir con su vinculación persistida.
    if scope not in SCOPES or event.affiliation.profile_type.code != SCOPES[scope][0]:
        raise PermanentProvisioningError('El ámbito de la orden no coincide con su vinculación.')
    checkpoint = _checkpoint_value(event.pk, 'support')
    if checkpoint.get('status') == 'CONFIRMED':
        return _mark_terminal(event.pk, simulated=checkpoint.get('simulated', False))
    result = perform_support(event.identity_id, operation, user=event.requested_by, dry_run=simulated, scope=event.payload.get('scope', 'GRADO'),
                             **event.payload.get('support_options', {}))
    _save_checkpoint(event.pk, 'support', 'CONFIRMED', simulated=result.simulated,
                     details={'action': result.action, 'batch_id': event.payload.get('batch_id')})
    return _mark_terminal(event.pk, simulated=result.simulated)


def _run_provision(event_id, event, graph, moodle, mailer, simulated, enqueue_moodle):
    """Aprovisiona Graph, licencia y Moodle; luego envía las credenciales."""

    payload = event.payload
    upn = payload.get("upn", "")
    if not upn:
        #FSRV03-6003: El UPN institucional debe estar definido antes de modificar cuentas remotas.
        raise PermanentProvisioningError("La identidad no tiene institutional_upn configurado.")

    if not _checkpoint_complete(event_id, "graph_user"):
        existing = graph.get_user(upn)
        if existing is not None:
            graph_result = existing
        else:
            #FSRV04-6004: La contraseña inicial se descarta y no se guarda en los puntos de control.
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
            #FSRV05-6005: La falta de SKU es un fallo permanente antes de cualquier escritura remota.
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
            #FSRV06-6006: Moodle continúa en su cola independiente; el trabajador no espera el resultado.
            enqueue_moodle(event_id)
        else:
            from celery import current_app

            current_app.send_task("provisioning.moodle_step", args=[event_id], queue="moodle")
        return OutboxEvent.Status.WAITING_SUBTASK

    if not _checkpoint_complete(event_id, "password_and_mail"):
        if not graph_state.get("details", {}).get("created", False) and not payload.get("send_welcome", False):
            #FSRV07-6007: Sin una nueva bienvenida explícita, las cuentas existentes conservan sus credenciales.
            _save_checkpoint(event_id, "password_and_mail", "SKIPPED_EXISTING_USER", simulated=simulated)
        else:
            #FSCOPEDWELCOME01-10502: La confirmación académica solicita bienvenida con credenciales como paso reanudable.
            _send_credentials(event_id, graph, mailer, payload, simulated)

    #FSRV08-6008: La simulación finaliza en un estado diferente del éxito real.
    return _mark_terminal(event_id, simulated=simulated)


def _run_suspend(event_id, event, graph, moodle, simulated, enqueue_moodle):
    """Retira accesos Moodle de esta vinculación y conserva los accesos compartidos requeridos."""

    if not event.payload.get('upn'):
        #FTTLSUSPEND03-10605: Una preinscripción sin UPN todavía no identifica accesos remotos que puedan suspenderse.
        for step in ('moodle', 'license', 'graph_disable'):
            _save_checkpoint(event_id, step, 'SKIPPED_NO_UPN', simulated=simulated)
        return _mark_terminal(event_id, simulated=simulated)

    if not _checkpoint_complete(event_id, "moodle"):
        _mark_waiting_subtask(event_id)
        if enqueue_moodle:
            #FSRV09-6009: La suspensión usa la misma continuación asíncrona de Moodle.
            enqueue_moodle(event_id)
        else:
            from celery import current_app

            current_app.send_task("provisioning.moodle_step", args=[event_id], queue="moodle")
        return OutboxEvent.Status.WAITING_SUBTASK

    if graph.get_user(event.payload.get('upn', '')) is None:
        #FTTLSUSPEND01-10602: Una preinscripción nunca aprovisionada no requiere retirar licencias ni deshabilitar cuentas ausentes.
        _save_checkpoint(event_id, 'license', 'SKIPPED_USER_MISSING', simulated=simulated)
        _save_checkpoint(event_id, 'graph_disable', 'SKIPPED_USER_MISSING', simulated=simulated)
        return _mark_terminal(event_id, simulated=simulated)

    #FSRV10-6010: Consulta las otras vinculaciones activas antes de decidir sobre accesos compartidos.
    other_active = Affiliation.objects.filter(
        identity_id=event.identity_id, is_active=True
    ).exclude(pk=event.affiliation_id).select_related("current_status")
    still_required = other_active.filter(current_status__requires_collaboration_license=True).exists()
    account_still_active = other_active.exists()
    upn = event.payload.get("upn", "")
    sku_id = settings.LUCY_M365_SKU_ID

    if still_required:
        if not _checkpoint_complete(event_id, "license"):
            #FSRV11-6011: Otra vinculación activa todavía requiere la licencia compartida.
            _save_checkpoint(event_id, "license", "SKIPPED_STILL_REQUIRED", simulated=simulated)
    elif not _checkpoint_complete(event_id, "license"):
        if not sku_id:
            #FSRV12-6012: Sin SKU no puede confirmarse el retiro de licencia.
            raise ConfigurationError("Falta configurar M365_A1_SKU_ID.")
        result = graph.remove_license(upn, sku_id)
        _save_checkpoint(
            event_id, "license", "CONFIRMED",
            simulated=simulated or bool(getattr(result, "simulated", False)),
            details={"assigned": False},
        )

    if account_still_active:
        if not _checkpoint_complete(event_id, "graph_disable"):
            #FSRV13-6013: Otras vinculaciones activas conservan habilitada la cuenta Entra.
            _save_checkpoint(event_id, "graph_disable", "SKIPPED_STILL_ACTIVE", simulated=simulated)
    elif not _checkpoint_complete(event_id, "graph_disable"):
        result = graph.disable_user(upn)
        _save_checkpoint(
            event_id, "graph_disable", "CONFIRMED",
            simulated=simulated or bool(getattr(result, "simulated", False)),
            details={"enabled": False},
        )

    #FSRV14-6014: TERMINATE suspende hasta que exista una política institucional de borrado remoto.
    if event.action == "TERMINATE" and not _checkpoint_complete(event_id, "graph_delete"):
        _save_checkpoint(event_id, "graph_delete", "SKIPPED_POLICY", simulated=simulated)
    return _mark_terminal(event_id, simulated=simulated)


def run_moodle_step(event_id, *, moodle=None, dry_run=None):
    """Ejecuta la continuación Moodle fuera de una transacción de base de datos.

    El evento conserva WAITING_SUBTASK durante las llamadas de red. Al
    confirmar la etapa, el resultado y la continuación PENDING se guardan
    juntos. El llamador puede solicitar otro despacho sin perder ese avance.
    Los fallos vuelven a la política persistente de reintentos para no dejar
    una espera indefinida.
    """

    event = _load_event(event_id)
    if event.status != OutboxEvent.Status.WAITING_SUBTASK:
        #FSRV15-6015: Una continuación duplicada no actúa después de cambiar el estado.
        return event.status
    simulated = settings.LUCY_DRY_RUN if dry_run is None else bool(dry_run)
    moodle = moodle or _moodle_client(simulated)
    try:
        outcome = _perform_moodle(event, moodle)
        #FSRV16-6016: El resultado Moodle y la continuación se confirman juntos después de la respuesta remota.
        with transaction.atomic():
            locked = OutboxEvent.objects.select_for_update().get(pk=event_id)
            if locked.status != OutboxEvent.Status.WAITING_SUBTASK:
                #FSRV26-6026: Un trabajador vencido no sobrescribe la recuperación realizada por el barrido.
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
    #FPROVISIONINGORCHESTRATOR008-8008: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Exception as error:
        #FSRV17-6017: Los fallos Moodle usan los mismos reintentos persistidos y acotados.
        return record_failure(event_id, error, checkpoint="moodle")


def _perform_moodle(event, moodle):
    """Converge usuario Moodle, matrículas y grupo de comisión opcional."""

    payload = event.payload
    upn = payload.get("upn", "")
    shortname = payload.get("course_shortname", "")
    if event.action == "PROVISION":
        if payload.get('identity_category', event.identity.category) == 'ORGANIZACIONAL' and not shortname:
            return {'status': 'SKIPPED_NOT_PEDAGOGICAL', 'simulated': False, 'details': {}}
        profile_code = payload.get("profile_type_code", event.affiliation.profile_type.code)
        status_code = payload.get("provisioning_status", event.affiliation.current_status.code)
        if profile_code == "ALUMNO" and status_code == "INGRE":
            shortnames, group_name = AdmissionCourseRule.resolve_courses(
                career=payload.get("career_code", event.affiliation.reference),
                modality=payload.get("modality", event.affiliation.modality),
                commission=payload.get("commission") or payload.get("group_name") or "",
            )
        else:
            shortnames = [shortname] if shortname else []
            group_name = payload.get("group_name")
        user = moodle.create_user(
            username=upn,
            first_name=payload["first_name"],
            last_name=payload["last_name"],
            email=upn,
            auth="oidc",
        )
        if not shortnames:
            return {"status": "SKIPPED_NO_COURSE", "simulated": user.simulated, "details": {}}
        simulated = user.simulated
        enrollments = []
        for shortname in shortnames:
            course = moodle.get_course_by_shortname(shortname)
            if course is None:
                #FSRV18-6018: Un curso ausente es un error de recurso, nunca un éxito supuesto.
                raise ResourceNotFoundError(f"No existe el curso Moodle {shortname}.")
            course_id = int(course["id"])
            enrollment = moodle.enrol_user(user.id, course_id, role_id=5)
            if not enrollment.success:
                #FPROVISIONINGORCHESTRATOR010-8010: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError(f"No se confirmó la matrícula en Moodle {shortname}.")
            simulated = simulated or enrollment.simulated
            if group_name:
                group_id = moodle.get_or_create_group(course_id, group_name)
                group = moodle.add_user_to_group(group_id, user.id, course_id=course_id)
                if not group.success:
                    #FPROVISIONINGORCHESTRATOR011-8011: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                    raise IntegrationError(f"No se confirmó el grupo en Moodle {shortname}.")
                simulated = simulated or group.simulated
            enrollments.append({"shortname": shortname, "course_id": course_id, "group": group_name})
        return {
            "status": "CONFIRMED",
            "simulated": simulated,
            "details": {"enrolled": True, "courses": enrollments},
        }

    user = moodle.get_user_by_username(upn)
    if user is None:
        return {"status": "SKIPPED_USER_MISSING", "simulated": False, "details": {}}
    if not shortname:
        return {"status": "SKIPPED_NO_COURSE", "simulated": user.simulated, "details": {}}
    course = moodle.get_course_by_shortname(shortname)
    if course is None:
        return {"status": "SKIPPED_COURSE_MISSING", "simulated": user.simulated, "details": {}}
    if Affiliation.objects.filter(identity_id=event.identity_id, is_active=True, reference=shortname,
                                  profile_type__code__in=['ALUMNO', 'POSGRADO', 'EXTENSION']).exclude(pk=event.affiliation_id).exists():
        #FTTLSUSPEND02-10603: Una matrícula compartida por otra vinculación activa no se retira.
        return {'status': 'SKIPPED_STILL_REQUIRED', 'simulated': False, 'details': {}}
    result = moodle.unenrol_user(user.id, int(course["id"]), role_id=5)
    return {
        "status": "CONFIRMED",
        "simulated": user.simulated or result.simulated,
        "details": {"unenrolled": result.success},
    }


def _send_credentials(event_id, graph, mailer, payload, simulated):
    """Regenera la clave y envía el correo como una etapa con resultado persistente."""

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
                #FSRV19-6019: Una configuración de correo inválida requiere intervención operativa.
                raise PermanentProvisioningError(f"Entrega de credenciales inválida ({error_type}).")
            #FSRV20-6020: Un fallo transitorio SMTP reintenta con una contraseña diferente.
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
        #FSRV21-6021: El secreto efímero se descarta también cuando falla Graph o SMTP.
        del password


def record_failure(event_id, error, *, checkpoint=None):
    """Persiste un reintento o fallo final sin registrar secretos remotos."""

    now = timezone.now()
    permanent = isinstance(
        error,
        (
            PermanentProvisioningError,
            PermissionDenied,
            ValidationError,
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
            #FSRV22-6022: Un fallo permanente o agotado conserva el bloqueo lógico de órdenes posteriores.
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
        event.last_error = f"{error.__class__.__name__}: falló una etapa de aprovisionamiento."
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
    """Agrega un resultado confirmado bajo bloqueo de la fila actual."""

    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        #FSRV23-6023: Cada paso combina sus resultados bajo bloqueo con la versión actual de la fila.
        results = dict(event.step_results)
        results[name] = {"status": status, "simulated": bool(simulated), "details": details or {}}
        event.step_results = results
        event.heartbeat_at = timezone.now()
        event.last_error = ""
        event.save(update_fields=["step_results", "heartbeat_at", "last_error"])


def _checkpoint_complete(event_id, name):
    """Comprueba si un paso ya fue confirmado u omitido de forma explícita."""

    value = _checkpoint_value(event_id, name)
    status = value.get("status", "")
    return status == "CONFIRMED" or status.startswith("SKIPPED_")


def _checkpoint_value(event_id, name):
    """Lee un punto de control del evento persistido actualmente."""

    event = OutboxEvent.objects.only("step_results").get(pk=event_id)
    return event.step_results.get(name, {})


def _mark_waiting_subtask(event_id):
    """Confirma WAITING_SUBTASK antes de publicar su continuación Moodle."""

    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        #FSRV24-6024: La espera queda persistida antes de publicar y puede ser recuperada por el barrido.
        event.status = OutboxEvent.Status.WAITING_SUBTASK
        event.locked_at = None
        event.heartbeat_at = timezone.now()
        event.save(update_fields=["status", "locked_at", "heartbeat_at"])


def _mark_terminal(event_id, *, simulated):
    """Finaliza el evento y registra su instante en una transacción breve."""

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
    """Lee el contexto del evento sin conservar un bloqueo durante llamadas a proveedores."""

    return OutboxEvent.objects.select_related(
        "identity", "requested_by", "affiliation", "affiliation__profile_type", "affiliation__current_status"
    ).get(pk=event_id)


def _temporary_password(length=32):
    """Genera una clave efímera con letras, dígitos y símbolos."""

    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _backoff(attempts):
    """Calcula una espera exponencial acotada con dispersión aleatoria."""

    import random

    return min(2**attempts * 30, 3600) + random.randint(0, 30)


def _graph_client(dry_run):
    """Construye el adaptador Graph desde la configuración sin acceder a la red."""

    return MicrosoftGraphClient(
        tenant_id=settings.LUCY_M365_TENANT_ID,
        client_id=settings.LUCY_M365_CLIENT_ID,
        client_secret=settings.LUCY_M365_CLIENT_SECRET,
        dry_run=dry_run,
    )


def _moodle_client(dry_run):
    """Construye el adaptador Moodle desde la configuración sin acceder a la red."""

    return MoodleRestClient(
        base_url=settings.LUCY_MOODLE_BASE_URL,
        token=settings.LUCY_MOODLE_TOKEN,
        dry_run=dry_run,
    )


def _mailer_client(dry_run):
    """Construye el adaptador SMTP seguro desde las variables de entorno."""

    return MailerClient(
        host=settings.LUCY_SMTP_HOST,
        port=settings.LUCY_SMTP_PORT,
        username=settings.LUCY_SMTP_USERNAME,
        password=settings.LUCY_SMTP_PASSWORD,
        from_email=settings.LUCY_SMTP_FROM_EMAIL,
        dry_run=dry_run,
    )
