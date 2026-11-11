"""Construcción de clientes sin conexiones durante la inicialización."""

from django.conf import settings

from .services.graph import MicrosoftGraphClient
from .services.mailer import MailerClient
from .services.moodle import MoodleRestClient
from .services.sial import SialClient


def build_graph(*, dry_run=None):
    return MicrosoftGraphClient(
        tenant_id=settings.LUCY_M365_TENANT_ID, client_id=settings.LUCY_M365_CLIENT_ID,
        client_secret=settings.LUCY_M365_CLIENT_SECRET, dry_run=settings.LUCY_DRY_RUN if dry_run is None else dry_run,
    )


def build_moodle(*, dry_run=None):
    return MoodleRestClient(base_url=settings.LUCY_MOODLE_BASE_URL, token=settings.LUCY_MOODLE_TOKEN,
                            dry_run=settings.LUCY_DRY_RUN if dry_run is None else dry_run)


def build_mailer(*, dry_run=None):
    return MailerClient(
        host=settings.LUCY_SMTP_HOST, port=settings.LUCY_SMTP_PORT,
        username=settings.LUCY_SMTP_USERNAME, password=settings.LUCY_SMTP_PASSWORD,
        from_email=settings.LUCY_SMTP_FROM_EMAIL, dry_run=settings.LUCY_DRY_RUN if dry_run is None else dry_run,
    )


def build_sial():
    return SialClient(base_url=settings.LUCY_SIAL_BASE_URL, username=settings.LUCY_SIAL_USERNAME,
                      password=settings.LUCY_SIAL_PASSWORD, dry_run=settings.LUCY_DRY_RUN)
