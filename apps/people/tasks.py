"""Tarea periódica de vencimientos; PostgreSQL conserva estados e intenciones Outbox."""

from celery import shared_task
from .ttl import process_expired_affiliations as sweep_expired


@shared_task(name='people.process_expired_affiliations', queue='provisioning')
def process_expired_affiliations(limit=500):
    """Procesa un lote sin esperar por filas ocupadas ni ejecutar APIs remotas."""
    return sweep_expired(limit=limit)
