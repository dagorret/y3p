"""Aplicación Celery integrada con la configuración Django y el descubrimiento de tareas."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("y3p")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
