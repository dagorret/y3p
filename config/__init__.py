"""Paquete del proyecto Django Y3P y exportación de la aplicación Celery."""

from .celery import app as celery_app

__all__ = ("celery_app",)
