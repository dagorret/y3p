"""Importa ingresantes del padrón local mediante servicios de dominio."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.people.models import ConfigurationError
from apps.platform_mocks.services import sync_sial_ingreso


class Command(BaseCommand):
    help = (
        'Ingiere MockSialRecord INGRESANTE y crea eventos pendientes. '
        'Usar solo con consumidores dirigidos a mocks o detenidos; no procesa el Outbox.'
    )

    def handle(self, *args, **options):
        try:
            result = sync_sial_ingreso()
        except (ValidationError, ConfigurationError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f"Ingesta SIAL: procesados={result['processed']}; "
            f"creados={result['created']}; existentes={result['existing']}."
        ))
