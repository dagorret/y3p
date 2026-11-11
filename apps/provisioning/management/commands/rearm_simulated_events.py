"""Rearma eventos simulados para una ejecución posterior contra integraciones reales."""

from celery import current_app
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.people.models import OutboxEvent


class Command(BaseCommand):
    """Limpia resultados simulados y devuelve esos eventos al estado pendiente."""

    help = "Rearma eventos SIMULATED para ejecutarlos contra los servicios reales."

    def add_arguments(self, parser):
        """Declara un modo de consulta que informa cuántas filas se rearmarían."""

        parser.add_argument(
            "--dry-run", action="store_true", help="Informa sin modificar eventos."
        )

    def handle(self, *args, **options):
        """Rearma filas atómicamente para impedir reinicios parciales de un evento."""

        queryset = OutboxEvent.objects.filter(status=OutboxEvent.Status.SIMULATED)
        count = queryset.count()
        if options["dry_run"]:
            #FCMD20-3020: La simulación cuenta filas sin modificarlas.
            self.stdout.write(f"Eventos simulados que se rearmarían: {count}.")
            return
        with transaction.atomic():
            #FCMD21-3021: Limpia resultados simulados y estado en una sola transacción.
            updated = queryset.update(
                status=OutboxEvent.Status.PENDING,
                step_results={},
                attempts=0,
                last_error="",
                locked_at=None,
                heartbeat_at=None,
                retry_after=None,
                processed_at=None,
            )
            if updated:
                #FCMD22-3022: La notificación sigue a la confirmación; la agenda periódica recupera publicaciones perdidas.
                transaction.on_commit(
                    lambda: current_app.send_task("provisioning.dispatch_outbox"),
                    robust=True,
                )
        self.stdout.write(self.style.SUCCESS(f"Eventos rearmados: {updated} de {count}."))
