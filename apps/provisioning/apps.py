"""Configuración Django de la orquestación asíncrona."""

from django.apps import AppConfig


class ProvisioningConfig(AppConfig):
    """Registra la orquestación de trabajadores sin definir tablas propias."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.provisioning"
    verbose_name = "Aprovisionamiento"
