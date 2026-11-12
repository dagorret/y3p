"""Entradas Celery del sincronizador relacional y su agenda persistida."""

from celery import shared_task
from .catalogs import sync_due_catalogs, sync_external_catalogs


@shared_task(name='integrations.sync_external_catalogs', queue='provisioning')
def sync_catalogs_task():
    """Actualiza ambos orígenes usando el arrendamiento persistido contra duplicados."""
    return sync_external_catalogs()


@shared_task(name='integrations.sync_due_catalogs', queue='provisioning')
def sync_due_catalogs_task():
    """Comprueba cada minuto qué agendas semanales deben ejecutarse."""
    return sync_due_catalogs()
