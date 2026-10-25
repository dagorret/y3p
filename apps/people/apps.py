"""Configuración Django para el módulo de identidades y vinculaciones."""

from django.apps import AppConfig


class PeopleConfig(AppConfig):
    """Registra los modelos de people y usa claves primarias BigAutoField."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.people"
    verbose_name = "Identidades y Vinculaciones"
