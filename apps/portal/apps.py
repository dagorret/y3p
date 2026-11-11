"""Configuración de la aplicación de portales reactivos HTMX."""

from django.apps import AppConfig


class PortalConfig(AppConfig):
    """Registra los portales y emplea enteros grandes como claves primarias."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal"
    verbose_name = "Portal Operativo"
