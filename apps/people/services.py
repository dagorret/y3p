"""Servicios de dominio para el ciclo de vida de afiliaciones.

Este módulo es el único punto autorizado para crear afiliaciones, transicionar
estados, vencer TTL y renovar vigencias. Cada operación que escribe usa una
transacción atómica; las transiciones y renovaciones bloquean la afiliación con
``SELECT FOR UPDATE`` y escriben su asiento histórico en la misma transacción.
"""

from datetime import timedelta

from celery import current_app
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from .models import (
    Affiliation,
    AffiliationStatusHistory,
    CrossProfileConstraint,
    Identity,
    OutboxEvent,
    SagaTransitionRule,
)


class TransitionError(Exception):
    """Error de dominio que indica una transición imposible o bloqueada.

    Si se lanza dentro de una operación ``atomic``, el decorador revierte todos
    los cambios de esa operación, incluido cualquier evento parcialmente creado.
    """

    #FSRV00-1000: Error de negocio de transición.


def _emit_outbox_event(affiliation, status):
    """Persist an intent and schedule dispatch after the producer commits.

    The payload captures only execution inputs; current cross-affiliation
    decisions remain live ORM reads in the consumer. Inserting the outbox row
    in the caller's transaction guarantees rollback atomicity. The commit hook
    only nudges Celery; periodic dispatch can recover if the broker is down.
    """

    if not status.provisioning_action:
        #FOUT01-5001: States without an external action produce no outbox row.
        return None

    identity = affiliation.identity
    try:
        recipient = identity.personal_profile.personal_email
    except ObjectDoesNotExist:
        #FOUT02-5002: Personal contact is optional, so store an empty recipient.
        recipient = ""

    #FOUT03-5003: Credentials and temporary passwords never enter durable payload data.
    payload = {
        "identifier": identity.identifier,
        "upn": identity.institutional_upn or "",
        "first_name": identity.first_name,
        "last_name": identity.last_name,
        "full_name": f"{identity.first_name} {identity.last_name}".strip(),
        "recipient": recipient,
        "course_shortname": (
            affiliation.reference if affiliation.profile_type.code == "ALUMNO" else ""
        ),
        "group_name": "",
        "modality": affiliation.modality,
        "provisioning_status": status.code,
        "requires_m365_license": status.requires_m365_license,
    }
    event = OutboxEvent.objects.create(
        action=status.provisioning_action,
        identity=identity,
        affiliation=affiliation,
        payload=payload,
    )
    #FOUT04-5004: The callback runs only after the database commit; row durability precedes broker I/O.
    transaction.on_commit(
        lambda: current_app.send_task("provisioning.dispatch_outbox"), robust=True
    )
    return event


def _ttl(status):
    """Calcula el instante de vencimiento de un estado o retorna ``None``.

    El TTL se calcula desde la hora actual consciente de zona horaria. La función
    no escribe en base de datos y su resultado se persiste dentro de la misma
    transacción que la afiliación que entra en ese estado.
    """

    if status.default_ttl_days is None:
        #FSRV14-1014: Un estado sin TTL no participa del barrido automático.
        return None
    return timezone.now() + timedelta(days=status.default_ttl_days)


def _has_affiliation_in(affiliation, profile_type, status, same_reference):
    """Busca otra afiliación de la identidad en el tipo y estado solicitados.

    No filtra por ``is_active``: un estado terminal es un hecho histórico que
    puede satisfacer reglas cruzadas. Si ``same_reference`` está activo, la
    otra fila debe coincidir en referencia. Solo ejecuta una consulta de lectura.
    """

    #FSRV01-1001: Consulta cruzada excluyendo la afiliación que se transiciona.
    query = Affiliation.objects.filter(
        identity_id=affiliation.identity_id,
        profile_type=profile_type,
        current_status=status,
    ).exclude(pk=affiliation.pk)
    if same_reference:
        #FSRV15-1015: La regla con referencia exige la misma carrera o unidad.
        query = query.filter(reference=affiliation.reference)
    return query.exists()


def _check_cross_constraints(affiliation, to_status):
    """Evalúa requisitos, prohibiciones y exclusiones antes del cambio de estado.

    Solo lee restricciones y afiliaciones. Si una regla no se cumple, eleva
    ``TransitionError`` para que la transacción exterior revierta sin cambios.
    Las reglas MUTEX se comprueban también desde el extremo inverso.
    """

    conditions = CrossProfileConstraint.Condition

    #FSRV16-1016: Reglas declaradas con la afiliación destino como objetivo.
    constraints = CrossProfileConstraint.objects.filter(
        target_profile_type=affiliation.profile_type,
        target_status=to_status,
    ).select_related("required_profile_type", "required_status")
    for constraint in constraints:
        exists = _has_affiliation_in(
            affiliation,
            constraint.required_profile_type,
            constraint.required_status,
            constraint.same_reference,
        )
        #FSRV02-1002: El estado requerido debe existir para REQUIRES.
        if constraint.condition_type == conditions.REQUIRES and not exists:
            #FSRV02-1002: Bloquea la transición si falta requisito cruzado.
            raise TransitionError(
                "Transición bloqueada: Requiere "
                f"{constraint.required_profile_type}:{constraint.required_status.code}."
            )
        #FSRV03-1003: FORBIDS y MUTEX no permiten la afiliación incompatible.
        if constraint.condition_type in (conditions.FORBIDS, conditions.MUTEX) and exists:
            #FSRV03-1003: Bloquea por incompatibilidad directa detectada.
            raise TransitionError(
                "Transición bloqueada: Incompatible con "
                f"{constraint.required_profile_type}:{constraint.required_status.code}."
            )

    #FSRV04-1004: Las reglas MUTEX también se hacen cumplir en sentido inverso.
    reverse_mutexes = CrossProfileConstraint.objects.filter(
        condition_type=conditions.MUTEX,
        required_profile_type=affiliation.profile_type,
        required_status=to_status,
    ).select_related("target_profile_type", "target_status")
    for constraint in reverse_mutexes:
        if _has_affiliation_in(
            affiliation,
            constraint.target_profile_type,
            constraint.target_status,
            constraint.same_reference,
        ):
            #FSRV04-1004: Bloquea por MUTEX declarado desde el extremo inverso.
            raise TransitionError(
                "Transición bloqueada: Excluyente con "
                f"{constraint.target_profile_type}:{constraint.target_status.code}."
            )


def _lock(affiliation_id):
    """Carga y bloquea una afiliación para serializar mutaciones concurrentes.

    Debe invocarse dentro de ``transaction.atomic``. PostgreSQL mantiene el lock
    de fila hasta commit o rollback, momento en que los demás procesos continúan
    y observan el estado confirmado.
    """

    #FSRV05-1005: Bloqueo pesimista de afiliación dentro de la transacción.
    return (
        Affiliation.objects.select_for_update()
        .select_related("profile_type", "current_status", "identity")
        .get(pk=affiliation_id)
    )


def _apply(affiliation, rule, *, changed_by, user, reason):
    """Aplica una regla y guarda cambio e historial de forma indivisible.

    El llamador debe poseer el lock de afiliación y una transacción activa. El
    estado, TTL y actividad se actualizan y luego se inserta la auditoría; si
    falla cualquiera de las escrituras, ``atomic`` revierte ambas.
    """

    if rule.requires_cross_validation:
        #FSRV17-1017: Ejecuta restricciones cruzadas antes de tocar la afiliación.
        _check_cross_constraints(affiliation, rule.to_status)

    from_status = affiliation.current_status
    affiliation.current_status = rule.to_status
    affiliation.expires_at = _ttl(rule.to_status)
    affiliation.is_active = not rule.to_status.is_terminal
    #FSRV18-1018: Actualiza estado operativo antes de insertar el evento, en la misma transacción.
    affiliation.save(update_fields=["current_status", "expires_at", "is_active"])

    #FSRV06-1006: Asiento append-only en la misma transacción que el cambio.
    AffiliationStatusHistory.objects.create(
        affiliation=affiliation,
        from_status=from_status,
        to_status=rule.to_status,
        rule=rule,
        changed_by=changed_by,
        changed_by_user=user,
        reason=reason,
    )
    _emit_outbox_event(affiliation, rule.to_status)
    return affiliation


@transaction.atomic
def create_affiliation(identity, profile_type, *, changed_by, user=None, reason="", **fields):
    """Crea una afiliación en su estado inicial y registra el hecho de alta.

    Bloquea la fila de identidad para serializar altas de tipos que no permiten
    instancias múltiples. La afiliación y su primer evento se confirman juntos;
    una configuración inválida o una violación de regla revierte ambos inserts.
    """

    #FSRV19-1019: Serializa validaciones de multiplicidad para una identidad.
    locked_identity = Identity.objects.select_for_update().get(pk=identity.pk)
    status = profile_type.initial_status
    #FSRV07-1007: ConfigurationError aborta antes de crear datos si falta estado inicial.
    affiliation = Affiliation(
        identity=locked_identity,
        profile_type=profile_type,
        current_status=status,
        expires_at=_ttl(status),
        **fields,
    )
    #FSRV20-1020: full_clean valida referencias y duplicidad antes del INSERT.
    affiliation.full_clean()
    affiliation.save()
    #FSRV21-1021: La fila de alta inicial forma parte de la misma transacción.
    AffiliationStatusHistory.objects.create(
        affiliation=affiliation,
        from_status=None,
        to_status=status,
        changed_by=changed_by,
        changed_by_user=user,
        reason=reason or "Alta",
    )
    _emit_outbox_event(affiliation, status)
    return affiliation


@transaction.atomic
def transition_affiliation(
    affiliation_id,
    to_status_code,
    *,
    changed_by,
    user=None,
    reason="",
    expected_from_code=None,
):
    """Transiciona una afiliación solo si el grafo autoriza la arista solicitada.

    La afiliación se bloquea primero. Cualquier error de dominio revierte el
    estado y el historial; ``expected_from_code`` sirve como control CAS para
    evitar aplicar decisiones tomadas sobre una pantalla desactualizada.
    """

    affiliation = _lock(affiliation_id)
    #FSRV08-1008: No se habilitan transiciones desde una afiliación inactiva.
    if not affiliation.is_active:
        #FSRV08-1008: Rechaza operación sobre afiliación inactiva.
        raise TransitionError("La vinculación está inactiva.")
    #FSRV09-1009: El estado observado por el llamador debe seguir vigente.
    if expected_from_code and affiliation.current_status.code != expected_from_code:
        #FSRV09-1009: Rechaza escritura basada en una lectura obsoleta.
        raise TransitionError(
            f"El estado cambió a {affiliation.current_status.code} "
            f"(se esperaba {expected_from_code})."
        )
    try:
        #FSRV22-1022: Solo se acepta una arista del mismo perfil y estado actual.
        rule = SagaTransitionRule.objects.select_related("to_status").get(
            profile_type=affiliation.profile_type,
            from_status=affiliation.current_status,
            to_status__code=to_status_code,
        )
    except SagaTransitionRule.DoesNotExist:
        #FSRV10-1010: No se permiten saltos no declarados en el grafo.
        raise TransitionError(
            f"Transición no permitida: {affiliation.current_status.code} → {to_status_code}."
        ) from None
    return _apply(
        affiliation,
        rule,
        changed_by=changed_by,
        user=user,
        reason=reason,
    )


@transaction.atomic
def expire_affiliation(affiliation_id, *, changed_by="job:ttl_sweeper"):
    """Aplica la única regla de vencimiento del estado actual si el TTL caducó.

    Revalida vencimiento y actividad después de obtener el lock. Si otro proceso
    ya renovó o transicionó la afiliación, retorna ``None`` sin escribir historial.
    """

    affiliation = _lock(affiliation_id)
    now = timezone.now()
    #FSRV11-1011: Revalidación estricta dentro del lock pesimista.
    if (
        not affiliation.is_active
        or affiliation.expires_at is None
        or affiliation.expires_at > now
    ):
        #FSRV11-1011: Operación idempotente cuando ya no está vencida.
        return None

    rule = (
        SagaTransitionRule.objects.select_related("to_status")
        .filter(
            profile_type=affiliation.profile_type,
            from_status=affiliation.current_status,
            is_expiration=True,
        )
        .first()
    )
    if rule is None:
        #FSRV12-1012: Vencida sin regla determinista de salida.
        raise TransitionError(
            f"El estado {affiliation.current_status} no tiene regla de vencimiento."
        )

    return _apply(
        affiliation,
        rule,
        changed_by=changed_by,
        user=None,
        reason=f"Vencimiento de TTL ({affiliation.expires_at:%Y-%m-%d})",
    )


@transaction.atomic
def renew_affiliation(
    affiliation_id,
    *,
    days=365,
    changed_by,
    user=None,
    reason="Renovación anual SIAL / Censo",
):
    """Extiende el TTL de una afiliación activa sin cambiar su estado.

    El lock impide que la renovación compita con una transición o vencimiento.
    La fecha y el evento ``from_status == to_status`` se confirman en una única
    transacción. El parámetro ``days`` debe ser positivo.
    """

    #FSRV23-1023: Una renovación debe extender una duración positiva.
    if days <= 0:
        #FSRV23-1023: Rechaza TTL cero o negativo antes de abrir cambios.
        raise TransitionError("La cantidad de días de renovación debe ser positiva.")

    affiliation = _lock(affiliation_id)
    #FSRV13-1013: Solo puede renovarse una afiliación activa.
    if not affiliation.is_active:
        #FSRV13-1013: Rechaza renovación de afiliación inactiva.
        raise TransitionError("No se puede renovar una vinculación inactiva.")

    affiliation.expires_at = timezone.now() + timedelta(days=days)
    #FSRV24-1024: Persiste únicamente el TTL; conserva estado y actividad.
    affiliation.save(update_fields=["expires_at"])

    #FSRV25-1025: La renovación también es un hecho auditable from == to.
    AffiliationStatusHistory.objects.create(
        affiliation=affiliation,
        from_status=affiliation.current_status,
        to_status=affiliation.current_status,
        rule=None,
        changed_by=changed_by,
        changed_by_user=user,
        reason=reason,
    )
    return affiliation
