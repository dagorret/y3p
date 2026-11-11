"""Genera la cohorte reproducible de 150 ingresantes sintéticos."""

from django.core.management.base import BaseCommand

from apps.platform_mocks.services import seed_mock_sial


class Command(BaseCommand):
    help = 'Siembra 150 registros SIAL sintéticos en platform_mocks (repetible).'

    def handle(self, *args, **options):
        created = seed_mock_sial()
        self.stdout.write(self.style.SUCCESS(f'Cohorte SIAL: 150 registros; nuevos: {created}.'))
