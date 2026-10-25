"""Requeue events completed in simulation after real integrations are enabled."""

from celery import current_app
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.people.models import OutboxEvent


class Command(BaseCommand):
    """Clear simulated checkpoints and return those outbox events to PENDING."""

    help = "Rearma eventos SIMULATED para ejecutarlos contra los servicios reales."

    def add_arguments(self, parser):
        """Declare a read-only mode that reports how many rows would be rearmed."""

        parser.add_argument(
            "--dry-run", action="store_true", help="Informa sin modificar eventos."
        )

    def handle(self, *args, **options):
        """Rearm rows atomically so no event is partially reset."""

        queryset = OutboxEvent.objects.filter(status=OutboxEvent.Status.SIMULATED)
        count = queryset.count()
        if options["dry_run"]:
            #FCMD20-3020: Dry-run only counts matching events and performs no write.
            self.stdout.write(f"Eventos simulados que se rearmarían: {count}.")
            return
        with transaction.atomic():
            #FCMD21-3021: Reset all simulated checkpoints and status in one transaction.
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
                #FCMD22-3022: Dispatch notification occurs after rearm commit; Beat remains recovery path.
                transaction.on_commit(
                    lambda: current_app.send_task("provisioning.dispatch_outbox"),
                    robust=True,
                )
        self.stdout.write(self.style.SUCCESS(f"Eventos rearmados: {updated} de {count}."))
