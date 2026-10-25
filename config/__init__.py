"""Y3P Django project package and Celery application export."""

from .celery import app as celery_app

__all__ = ("celery_app",)
