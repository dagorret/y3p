"""Comando para barrer vencimientos TTL de afiliaciones activas."""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.people.models import Affiliation, SagaTransitionRule
from apps.people.services import TransitionError, expire_affiliation


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Busca TTL caducados e invoca el servicio transaccional por afiliación.

    La selección inicial es de solo lectura. Cada vencimiento real se procesa
    en una transacción propia, bloquea y revalida su afiliación en el servicio,
    por lo que un fallo no revierte el lote completo ni afecta a otros registros.
    """

    help = "Aplica la regla de vencimiento a las vinculaciones activas con TTL caducado."

    def add_arguments(self, parser):
        """Registra modo de simulación y tamaño máximo del lote por iteración."""

        parser.add_argument(
            "--dry-run", action="store_true", help="Simula sin aplicar cambios."
        )
        parser.add_argument(
            "--batch", type=int, default=500, help="Tamaño del lote a procesar."
        )

    def handle(self, *args, **opts):
        """Reporta huérfanas y procesa o simula los vencimientos elegibles.

        En ``--dry-run`` no se llama a servicios de escritura. En modo normal,
        cada afiliación se confirma de forma independiente y los errores de
        dominio quedan reportados sin bloquear las demás filas del barrido.
        """

        now = timezone.now()
        dry_run = opts["dry_run"]
        batch = opts["batch"]
        #FCMD05-3005: Un lote positivo es necesario para que la paginación avance.
        if batch <= 0:
            #FCMD05-3005: Rechaza tamaño inválido antes de consultar o mutar datos.
            raise CommandError("--batch debe ser un entero positivo.")

        #FCMD01-3001: Subconsulta correlacionada detecta regla de expiración por estado.
        expiration_rule = SagaTransitionRule.objects.filter(
            from_status=OuterRef("current_status"), is_expiration=True
        )
        expired = Affiliation.objects.filter(is_active=True, expires_at__lte=now)

        #FCMD06-3006: Las filas vencidas sin regla se reportan fuera del lote ejecutable.
        orphans = (
            expired.exclude(Exists(expiration_rule))
            .values("current_status__profile_type__code", "current_status__code")
            .distinct()
        )
        for orphan in orphans:
            #FCMD02-3002: Una anomalía se registra una vez por combinación de tipo y estado.
            message = (
                f"ANOMALÍA: Estado {orphan['current_status__profile_type__code']}:"
                f"{orphan['current_status__code']} tiene TTL pero no regla de vencimiento."
            )
            logger.warning(message)
            self.stdout.write(self.style.WARNING(message))

        #FCMD07-3007: Solo las filas con regla explícita son candidatas a transición.
        processable = expired.filter(Exists(expiration_rule)).order_by("expires_at")
        failed = set()
        done = 0

        while True:
            #FCMD03-3003: Excluye fallidas para evitar repetirlas en este mismo barrido.
            ids = list(
                processable.exclude(pk__in=failed).values_list("pk", flat=True)[:batch]
            )
            if not ids:
                #FCMD08-3008: Un lote vacío termina el procesamiento de forma finita.
                break

            if dry_run:
                #FCMD09-3009: El modo simulación solo lee y reporta; nunca llama a expire_affiliation.
                affiliations = Affiliation.objects.filter(pk__in=ids).select_related(
                    "identity", "current_status"
                )
                for affiliation in affiliations:
                    self.stdout.write(
                        f"[DRY-RUN] {affiliation.pk} {affiliation.identity.identifier} "
                        f"{affiliation.current_status} vencería."
                    )
                done += len(ids)
                #FCMD10-3010: Una sola consulta por dry-run evita recorrer el mismo lote indefinidamente.
                break

            for affiliation_id in ids:
                try:
                    #FCMD04-3004: El servicio abre transacción, bloquea y revalida el TTL.
                    if expire_affiliation(affiliation_id) is not None:
                        done += 1
                except TransitionError as error:
                    #FCMD11-3011: Aísla errores de dominio por afiliación y continúa el lote.
                    failed.add(affiliation_id)
                    logger.error(
                        "No se pudo vencer la vinculación %s: %s", affiliation_id, error
                    )
                    self.stdout.write(
                        self.style.ERROR(f"Error en vinculación {affiliation_id}: {error}")
                    )

        #FCMD12-3012: El resumen conserva el corte temporal usado en la selección.
        self.stdout.write(
            self.style.SUCCESS(
                f"Barrido completado. Vencidas: {done}. Con error: {len(failed)}. "
                f"Corte: {now:%Y-%m-%d %H:%M}."
            )
        )
