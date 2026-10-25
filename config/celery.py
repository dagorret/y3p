"""Celery application wired to Django settings and task autodiscovery."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("y3p")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
